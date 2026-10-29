"""无界面命令行入口：批量解密（供资源管理器右键菜单调用）与自检。

命令示例::

    DLP-Pass.exe --decrypt "D:\\资料\\a.docx" "D:\\资料\\b.pdf"
    DLP-Pass.exe --in-place --notify --decrypt "D:\\资料"
    DLP-Pass.exe --selftest [待解密的加密文件...]

资源管理器对多选文件通常会逐项启动菜单命令。使用 ``--shell-batch`` 时，
多个进程会通过本机命名管道把同一批次的路径汇总到首个进程，由该进程
统一解密并只弹出一个结果提示框（命名管道仅用于本机进程间通信）。
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import logging
import os
import queue
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .config import load_runtime_config
from .core.decrypt_service import DecryptService
from .core.file_collector import collect_entries
from .core.mapping_loader import load_latest
from .core.models import DecryptTask
from .core.output_mode import OutputMode, plan_decrypt_tasks, validate_output_dir
from .core.source_reader import SourceReader
from .core.verifier import EncryptionVerifier
from .core.writer_factory import DisguisedWriterFactory
from .logging_setup import setup_logging

_logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 右键多选聚合：等待窗口内没有新路径即认为本批次收集完毕
_SHELL_BATCH_QUIET_SECONDS = 1.0
_SHELL_BATCH_MAX_SECONDS = 20.0
_SHELL_BATCH_CONNECT_RETRIES = 120
_SHELL_BATCH_CONNECT_INTERVAL = 0.1
_SHELL_BATCH_ACK_TIMEOUT = 5.0

_IDYES = 6


@dataclass
class DecryptSummary:
    """一次命令行解密的结果统计（用于日志与提示框）。"""

    sources: int = 0
    tasks: int = 0
    success: int = 0
    failed: int = 0
    skipped_plain: int = 0
    skipped_conflict: int = 0
    missing: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    output_root: Path | None = None
    mode: OutputMode | None = None
    cancelled: bool = False
    fatal_error: str | None = None
    log_path: Path | None = None

    @property
    def ok(self) -> bool:
        """是否完全成功（未加密文件被跳过不算失败）。"""
        return (
            self.fatal_error is None
            and not self.missing
            and self.failed == 0
            and self.skipped_conflict == 0
        )


@dataclass
class _ShellBatch:
    """右键批次收集结果。

    ``handled_elsewhere`` 为 ``True`` 表示当前进程只是转发者，路径已交给
    同批次的接收进程，当前进程应直接退出。
    """

    paths: list[str]
    handled_elsewhere: bool = False
    listener: object | None = None
    collecting: threading.Event | None = None

    def finish(self) -> None:
        """结束收集并关闭命名管道接收端（可重复调用）。"""
        if self.collecting is not None:
            self.collecting.clear()
        listener = self.listener
        self.listener = None
        if listener is not None:
            try:
                listener.close()
            except Exception:
                pass


class _ProgressSink:
    """进度回调接口；无界面模式与进度窗口都实现这组方法。"""

    def scan(self, scanned: int, candidates: int) -> None:
        pass

    def plan(self, total: int) -> None:
        pass

    def start_file(self, index: int, total: int, name: str) -> None:
        pass

    def finish_file(self, index: int, total: int, success: bool) -> None:
        pass

    def should_cancel(self) -> bool:
        return False


class _NullProgress(_ProgressSink):
    """无界面模式下的空进度回调。"""


class _ArgumentParser(argparse.ArgumentParser):
    """在 ``--noconsole`` 发布版中 stdout/stderr 可能为 ``None``，避免打印帮助时崩溃。"""

    def _print_message(self, message: str | None, file=None) -> None:  # type: ignore[override]
        if not message:
            return
        target = file if file is not None else sys.stdout
        if target is None:
            return
        try:
            target.write(message)
        except (OSError, ValueError):
            pass


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="DLP-Pass",
        description="DLP-Pass 无界面解密命令行（不带参数启动时显示图形界面）。",
    )
    parser.add_argument(
        "--decrypt",
        action="store_true",
        help="执行无界面批量解密；待解密路径放在命令末尾。",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="原地解密覆盖源文件（校验为明文后才替换，失败时源文件保持不变）。",
    )
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        help="输出到指定目录（默认使用 config.json 的 default_output_dir）。",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="显示独立进度窗口（不打开主界面，适合右键菜单）。",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="完成后显示 Windows 消息框（适合 --noconsole 发布版）。",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="开始前显示确认对话框（原地覆盖时建议启用）。",
    )
    parser.add_argument(
        "--shell-batch",
        action="store_true",
        help="汇总资源管理器多选产生的多个进程，再统一解密。",
    )
    parser.add_argument(
        "--include-plain",
        action="store_true",
        help="未检测到加密特征的文件也执行导出（默认跳过）。",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="无界面自检：加载映射并对给定文件执行完整解密流程。",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="待解密的文件或文件夹路径，可一次传入多个。",
    )
    return parser


# ---------------------------------------------------------------- 路径与批次


def _normalize_input_paths(raw_paths: Sequence[str]) -> list[str]:
    """清理引号/环境变量、转为绝对路径并按 Windows 大小写规则去重。"""
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_paths:
        text = str(raw).strip()
        if len(text) >= 2 and text[0] == text[-1] == '"':
            text = text[1:-1]
        if not text:
            continue
        path = Path(os.path.expandvars(os.path.expanduser(text)))
        if not path.is_absolute():
            path = Path.cwd() / path
        try:
            path = Path(os.path.abspath(str(path)))
        except (OSError, ValueError):
            continue
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        result.append(str(path))
    return result


def _shell_pipe_name() -> str:
    """当前用户 / 会话专用的命名管道名。"""
    identity = "|".join(
        (
            os.environ.get("USERDOMAIN", ""),
            os.environ.get("USERNAME", ""),
            os.environ.get("SESSIONNAME", ""),
            os.environ.get("USERPROFILE", ""),
        )
    )
    digest = hashlib.sha1(identity.encode("utf-8", "replace")).hexdigest()[:16]
    return r"\\.\pipe\dlp-pass-shell-" + digest


def _forward_shell_paths(address: str, paths: Sequence[str]) -> bool:
    """把路径发送给已存在的接收进程；对方确认接收时返回 ``True``。"""
    from multiprocessing.connection import Client

    conn = None
    for _ in range(_SHELL_BATCH_CONNECT_RETRIES):
        try:
            conn = Client(address, family="AF_PIPE")
            break
        except OSError:
            time.sleep(_SHELL_BATCH_CONNECT_INTERVAL)
    if conn is None:
        return False

    try:
        conn.send(list(paths))
        if not conn.poll(_SHELL_BATCH_ACK_TIMEOUT):
            return False
        return bool(conn.recv())
    except (OSError, EOFError, ValueError):
        return False
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _collect_shell_paths(paths: list[str]) -> _ShellBatch:
    """右键多选聚合：首个进程收集，其余进程转发后退出。"""
    if os.name != "nt":
        return _ShellBatch(paths=list(paths))

    from multiprocessing.connection import Listener

    address = _shell_pipe_name()
    try:
        # backlog 需覆盖 Windows 资源管理器单次最多 100 个多选项
        listener = Listener(address, family="AF_PIPE", backlog=128)
    except OSError:
        _logger.info("右键批次接收端已存在，尝试转发当前路径")
        if _forward_shell_paths(address, paths):
            return _ShellBatch(paths=[], handled_elsewhere=True)
        _logger.warning("转发失败，当前进程独立处理所选路径")
        return _ShellBatch(paths=list(paths))

    inbox: "queue.Queue[list[str]]" = queue.Queue()
    collecting = threading.Event()
    collecting.set()

    def accept_loop() -> None:
        while True:
            try:
                conn = listener.accept()
            except Exception:
                return
            accepted = False
            try:
                payload = conn.recv()
                if collecting.is_set() and isinstance(payload, (list, tuple)):
                    inbox.put([str(item) for item in payload])
                    accepted = True
                conn.send(accepted)
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    threading.Thread(
        target=accept_loop, name="dlp-pass-shell-accept", daemon=True
    ).start()

    merged = list(paths)
    deadline = time.monotonic() + _SHELL_BATCH_MAX_SECONDS
    last_received = time.monotonic()
    while time.monotonic() < deadline:
        try:
            merged.extend(inbox.get(timeout=0.2))
            last_received = time.monotonic()
        except queue.Empty:
            if time.monotonic() - last_received >= _SHELL_BATCH_QUIET_SECONDS:
                break

    collecting.clear()  # 收集窗口结束；迟到的进程会收到 False 并自行处理
    merged = _normalize_input_paths(merged)
    _logger.info("右键批次汇总：%d 个路径", len(merged))
    return _ShellBatch(paths=merged, listener=listener, collecting=collecting)


# ---------------------------------------------------------------- 提示与统计


def _message_box(
    text: str,
    title: str,
    *,
    error: bool = False,
    confirm: bool = False,
) -> int:
    """显示 Windows 消息框；返回 MessageBoxW 的按钮 ID（非 Windows 返回 0）。"""
    if os.name != "nt":
        return 0
    flags = 0x00010000 | 0x00040000  # MB_SETFOREGROUND | MB_TOPMOST
    if confirm:
        flags |= 0x00000004 | 0x00000030 | 0x00000100  # YESNO | WARNING | DEFBUTTON2
    else:
        flags |= 0x00000010 if error else 0x00000040  # ERROR / INFORMATION
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.MessageBoxW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint,
        ]
        user32.MessageBoxW.restype = ctypes.c_int
        return int(user32.MessageBoxW(None, text, title, flags))
    except Exception:
        _logger.exception("显示消息框失败")
        return 0


def _confirm_decrypt(count: int, mode: OutputMode, output_root: Path | None) -> bool:
    if mode is OutputMode.IN_PLACE:
        target = "解密结果将覆盖原文件（仅在校验为明文后替换；失败时原文件保持不变）。"
    else:
        target = f"解密结果将写入：{output_root}"
    text = f"即将解密 {count} 个文件。\n\n{target}\n\n是否继续？"
    return _message_box(text, "确认 DLP-Pass 解密", confirm=True) == _IDYES


def _format_summary(summary: DecryptSummary) -> str:
    lines: list[str] = []
    if summary.fatal_error:
        lines.append(f"错误：{summary.fatal_error}")

    missing_note = f"（缺失 {len(summary.missing)} 个）" if summary.missing else ""
    lines.append(f"输入路径：{summary.sources} 个{missing_note}")
    if summary.mode is OutputMode.IN_PLACE:
        lines.append("输出方式：原地解密覆盖")
    elif summary.output_root is not None:
        lines.append(f"输出目录：{summary.output_root}")

    lines.append(f"发现待解密文件：{summary.tasks} 个")
    lines.append(f"成功：{summary.success} 个")
    if summary.failed:
        lines.append(f"失败：{summary.failed} 个")
    if summary.skipped_plain:
        lines.append(f"跳过（未加密）：{summary.skipped_plain} 个")
    if summary.skipped_conflict:
        lines.append(f"跳过（输出冲突）：{summary.skipped_conflict} 个")
    if summary.cancelled:
        lines.append("已由用户取消")
    if (
        summary.tasks == 0
        and not summary.fatal_error
        and not summary.missing
        and not summary.cancelled
    ):
        lines.append("没有需要解密的文件（可能全部未加密）。")

    if summary.missing:
        lines.append("")
        lines.append("未找到的路径：")
        for path in summary.missing[:5]:
            lines.append(f"· {path}")
        if len(summary.missing) > 5:
            lines.append(f"· …… 另有 {len(summary.missing) - 5} 个")

    if summary.failures:
        lines.append("")
        lines.append("失败明细：")
        for name, error in summary.failures[:8]:
            lines.append(f"· {name}：{error}")
        if len(summary.failures) > 8:
            lines.append(f"· …… 另有 {len(summary.failures) - 8} 个")

    if summary.log_path is not None:
        lines.append("")
        lines.append(f"日志：{summary.log_path}")
    return "\n".join(lines)


def _notify_summary(summary: DecryptSummary) -> None:
    if summary.cancelled and summary.ok:
        title = "DLP-Pass 已取消"
    elif summary.ok:
        title = "DLP-Pass 解密完成"
    else:
        title = "DLP-Pass 解密失败"
    _message_box(_format_summary(summary), title, error=not summary.ok)


# ---------------------------------------------------------------- 解密流程


def _scan_entries(
    paths: Sequence[str],
    verifier: EncryptionVerifier,
    include_plain: bool,
    summary: DecryptSummary,
    progress: "_ProgressSink",
) -> list:
    """展开路径并检测加密状态；默认跳过未加密文件。"""
    entries = []
    scanned = 0
    for scanned, entry in enumerate(collect_entries(paths), 1):
        if progress.should_cancel():
            summary.cancelled = True
            _logger.info("用户取消了扫描")
            break
        try:
            encrypted = verifier.is_encrypted(entry.source)
        except Exception:
            _logger.exception("检测加密状态失败，按密文处理: %s", entry.source)
            encrypted = None
        if encrypted is False and not include_plain:
            summary.skipped_plain += 1
            _logger.info("跳过未加密文件: %s", entry.source)
        else:
            entries.append(entry)
        # 进度窗口按每 10 个文件刷新一次，避免大批量扫描时信号过多
        if scanned == 1 or scanned % 10 == 0:
            progress.scan(scanned, len(entries))
        if scanned % 100 == 0:
            _logger.info("已扫描 %d 个文件（待解密 %d 个）…", scanned, len(entries))
    if not summary.cancelled:
        progress.scan(scanned, len(entries))
    return entries


def _execute_decrypt(
    raw_paths: Sequence[str],
    args: argparse.Namespace,
    summary: DecryptSummary,
    progress: "_ProgressSink",
) -> int:
    config = load_runtime_config(PROJECT_ROOT)
    mapping = load_latest(config, PROJECT_ROOT)
    _logger.info("映射规则: %s", mapping.describe())

    paths = _normalize_input_paths(raw_paths)
    summary.sources = len(paths)
    existing: list[str] = []
    for path in paths:
        if Path(path).exists():
            existing.append(path)
        else:
            summary.missing.append(path)
            _logger.error("路径不存在: %s", path)

    if args.in_place and args.output_dir:
        summary.fatal_error = "--in-place 与 --output-dir 不能同时使用。"
        _logger.error(summary.fatal_error)
        progress.plan(0)
        return 2

    mode = OutputMode.IN_PLACE if args.in_place else OutputMode.DIRECTORY
    summary.mode = mode
    output_root: Path | None = None
    if mode is OutputMode.DIRECTORY:
        requested = args.output_dir or str(config.default_output_dir)
        valid, message, resolved = validate_output_dir(requested)
        if not valid:
            summary.fatal_error = message
            _logger.error("输出目录无效: %s", message)
            progress.plan(0)
            return 2
        output_root = resolved
        summary.output_root = output_root

    verifier = EncryptionVerifier(config)
    service: DecryptService | None = None
    try:
        if not existing:
            _logger.info("没有可处理的路径")
            progress.plan(0)
            return 0 if not summary.missing else 1

        entries = _scan_entries(existing, verifier, args.include_plain, summary, progress)
        if summary.cancelled:
            progress.plan(0)
            return 0
        tasks, conflicts = plan_decrypt_tasks(entries, mode, output_root)
        summary.skipped_conflict = len(conflicts)
        for item_id, note in conflicts:
            _logger.warning("跳过 %s: %s", item_id, note)
        summary.tasks = len(tasks)
        total = len(tasks)
        progress.plan(total)

        if not tasks:
            _logger.info("没有需要解密的文件（可能全部未加密）")
            return 0 if summary.ok else 1

        if args.confirm and not _confirm_decrypt(total, mode, output_root):
            summary.cancelled = True
            _logger.info("用户取消了本次解密")
            return 0

        service = DecryptService(
            mapping=mapping,
            verifier=verifier,
            writer_factory=DisguisedWriterFactory(config),
            source_reader=SourceReader(config.encryption_marker),
        )
        # 同后缀连续处理可复用常驻伪装进程（与图形界面的排序规则一致）
        for index, task in enumerate(
            sorted(tasks, key=lambda item: item.suffix.lower()), 1
        ):
            if progress.should_cancel():
                summary.cancelled = True
                _logger.info("用户取消了本次解密")
                break
            progress.start_file(index, total, task.source.name)
            _logger.info("开始解密: %s", task.source)
            outcome = service.decrypt_file(
                task,
                log=lambda message, source=task.source: _logger.info(
                    "[%s] %s", source.name, message
                ),
                is_cancelled=progress.should_cancel,
            )
            if outcome.cancelled:
                summary.cancelled = True
                progress.finish_file(index - 1, total, False)
                _logger.info("已取消: %s", task.source)
                break
            if outcome.success:
                summary.success += 1
                _logger.info("解密成功: %s -> %s", task.source, outcome.output)
            else:
                summary.failed += 1
                error = outcome.error or "未知错误"
                summary.failures.append((task.source.name, error))
                _logger.error("解密失败: %s (%s)", task.source, error)
            progress.finish_file(index, total, outcome.success)
        return 0 if summary.ok else 1
    except Exception as exc:
        summary.fatal_error = f"内部错误: {exc}"
        _logger.exception("命令行解密异常")
        return 1
    finally:
        if service is not None:
            service.close()
        verifier.close()


def _decrypt_paths(
    raw_paths: Sequence[str], args: argparse.Namespace, log_path: Path | None
) -> int:
    summary = DecryptSummary(log_path=log_path)
    if args.progress:
        try:
            from .ui_qt.progress import run_with_progress
        except Exception:
            _logger.exception("无法加载进度窗口，回退到无界面模式")
        else:
            code = run_with_progress(
                lambda progress: _execute_decrypt(raw_paths, args, summary, progress),
                summary,
            )
            if args.notify:
                _notify_summary(summary)
            return code

    code = _execute_decrypt(raw_paths, args, summary, _NullProgress())
    if args.notify:
        _notify_summary(summary)
    return code


def _run_decrypt(args: argparse.Namespace, log_path: Path | None) -> int:
    if args.shell_batch:
        batch = _collect_shell_paths(list(args.paths))
    else:
        batch = _ShellBatch(paths=list(args.paths))

    if batch.handled_elsewhere:
        _logger.info("所选路径已交给同批次的另一个进程处理")
        batch.finish()
        return 0
    try:
        return _decrypt_paths(batch.paths, args, log_path)
    finally:
        batch.finish()


# ---------------------------------------------------------------- 自检


def _run_selftest(paths: Sequence[str]) -> int:
    """无界面自检：加载映射规则 + 对给定文件执行完整解密流程。"""
    logger = logging.getLogger("selftest")
    config = load_runtime_config(PROJECT_ROOT)
    mapping = load_latest(config, PROJECT_ROOT)
    logger.info("[selftest] 映射规则: %s", mapping.describe())

    verifier = EncryptionVerifier(config)
    service = DecryptService(
        mapping=mapping,
        verifier=verifier,
        writer_factory=DisguisedWriterFactory(config),
        source_reader=SourceReader(config.encryption_marker),
    )
    out_dir = Path(tempfile.gettempdir()) / "dlp_pass_selftest"

    failed = 0
    try:
        for raw in paths:
            source = Path(raw)
            task = DecryptTask(
                source=source,
                output=out_dir / source.name,
                suffix=source.suffix.lower(),
            )
            outcome = service.decrypt_file(
                task, log=lambda message: logger.info("[selftest] %s", message)
            )
            if outcome.success:
                logger.info("[selftest] 解密成功: %s -> %s", source, task.output)
            else:
                failed += 1
                logger.error("[selftest] 解密失败: %s (%s)", source, outcome.error)
    finally:
        verifier.close()
        service.close()

    if not paths:
        logger.info("[selftest] 未提供待解密文件，仅完成映射加载检查")
    logger.info("[selftest] 结束：%d 个文件，失败 %d 个", len(paths), failed)
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口；返回进程退出码。"""
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    try:
        # 允许选项与多个路径混排（Python 3.8+）
        args = parser.parse_intermixed_args(arguments)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    log_path = setup_logging()

    if args.selftest:
        return _run_selftest(args.paths)

    if not args.decrypt:
        parser.print_help()
        return 2

    if not args.paths:
        message = "缺少待解密的文件或文件夹路径。"
        _logger.error(message)
        if args.notify:
            _message_box(message, "DLP-Pass 参数错误", error=True)
        return 2

    if args.in_place and args.output_dir:
        message = "--in-place 与 --output-dir 不能同时使用。"
        _logger.error(message)
        if args.notify:
            _message_box(message, "DLP-Pass 参数错误", error=True)
        return 2

    try:
        return _run_decrypt(args, log_path)
    except Exception as exc:
        _logger.exception("命令行处理失败")
        if args.notify:
            _message_box(f"内部错误：{exc}", "DLP-Pass 解密失败", error=True)
        return 1
