"""核心解密服务：与界面完全解耦，可独立在命令行 / 测试中调用。

流程（与原脚本保持一致）
-----------------------
1. 用当前 Python 进程读取源文件，得到已解密的明文内存流；
2. 从本地映射表（mappings/ 当前版本）取该后缀对应的伪装进程队列（主进程 -> 备用进程）；
3. 复用 / 构建伪装进程，将明文流经 stdin 写入 ``*.pass`` 临时文件；
4. 将 ``*.pass`` 重命名为最终文件（等价于原脚本的 ``move /y``）；
5. 用 PowerShell（非白名单视角）校验输出文件是否仍呈密文特征
   （标识串命中或后缀应有明文签名缺失），
   若仍为密文则删除输出并尝试下一个备用进程。

批处理进程复用（性能优化）
--------------------------
调用方把任务按后缀分组排序后连续调用 :meth:`decrypt_file`
（见 ``dlp_pass.ui_qt.workers.DecryptWorker``）：同一后缀的多个文件只启动一次
伪装进程并在组内复用（常驻批处理协议见 :class:`_WriterSession`），后缀切换或
全部处理完成后由 :meth:`close` 关闭，避免逐文件「创建 -> 通信 -> 销毁」的开销。

输出模式（见 :mod:`dlp_pass.core.output_mode`）
--------------------------------------------
- 输出到指定目录：``task.output`` 位于输出目录内，保留相对目录结构，原始文件不动；
- 原地解密覆盖：``task.in_place=True`` 且 ``task.output == task.source``。
  此时先校验临时 ``*.pass`` 文件已是明文，再重命名覆盖原文件，
  校验不通过时原文件绝不被动（重命名不改变内容，故无需二次校验）。
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import subprocess
from pathlib import Path
from typing import Callable

from .mapping_loader import MappingSet
from .models import DecryptOutcome, DecryptTask
from .source_reader import SourceReader
from .verifier import EncryptionVerifier
from .writer_factory import DisguisedWriterFactory

_logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000

LogCallback = Callable[[str], None]
CancelCallback = Callable[[], bool]


class _WriterSession:
    """常驻伪装进程会话：同一进程串行处理多个文件的落盘请求。

    协议（与 ``packaging/writer_stub.py`` 的批处理模式对应）：
    请求为一行 ``<base64 路径> <字节数>`` 加紧跟其后的原始数据，
    应答为 ``OK``（成功）或 ``ERR <base64 消息>``（失败）；
    关闭 stdin（EOF）即通知子进程退出。
    """

    def __init__(self, exe_path: Path):
        self._proc = subprocess.Popen(
            [str(exe_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )

    def is_alive(self) -> bool:
        return self._proc.poll() is None

    def write_file(self, target: Path, data: bytes) -> tuple[bool, str]:
        """请求伪装进程把 ``data`` 写入 ``target``；返回 ``(是否成功, 说明)``。"""
        if not self.is_alive():
            return False, "伪装进程已退出"
        assert self._proc.stdin is not None and self._proc.stdout is not None
        header = base64.b64encode(os.fsencode(str(target)))
        header += b" " + str(len(data)).encode("ascii") + b"\n"
        try:
            self._proc.stdin.write(header)
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
            reply = self._proc.stdout.readline().strip()
        except (OSError, ValueError) as exc:
            return False, f"伪装进程通信失败: {exc}"
        if reply == b"OK":
            return True, ""
        if reply.startswith(b"ERR "):
            try:
                return False, base64.b64decode(reply[4:]).decode("utf-8", "replace")
            except Exception:
                return False, "伪装进程写盘失败（错误信息无法解析）"
        return False, "伪装进程未确认写盘（可能已退出）"

    def close(self) -> None:
        """关闭 stdin 通知子进程退出；超时未退出则强制终止。"""
        proc = self._proc
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        try:
            if proc.poll() is None:
                proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except OSError:
            pass


class DecryptService:
    """单个文件的解密导出服务（批处理下按后缀复用常驻伪装进程）。"""

    def __init__(
        self,
        mapping: MappingSet,
        verifier: EncryptionVerifier,
        writer_factory: DisguisedWriterFactory,
        source_reader: SourceReader | None = None,
    ):
        self._mapping = mapping
        self._verifier = verifier
        self._writers = writer_factory
        # 源读取器：发布版进程非 DLP 白名单时自动委派 cmd / python 读取明文
        self._reader = source_reader or SourceReader()
        # 当前后缀分组内已启动的常驻伪装进程会话（进程名 -> 会话）
        self._sessions: dict[str, _WriterSession] = {}
        self._active_suffix: str | None = None

    def close(self) -> None:
        """关闭当前所有常驻伪装进程（批量结束 / 取消 / 退出前调用，可重复调用）。"""
        self._close_sessions()
        self._active_suffix = None

    def decrypt_file(
        self,
        task: DecryptTask,
        log: LogCallback | None = None,
        is_cancelled: CancelCallback | None = None,
    ) -> DecryptOutcome:
        """执行一次解密任务，返回结果（不抛异常）。

        调用方应保证同后缀任务连续调用以复用伪装进程；后缀变化时会自动
        关闭上一分组的常驻进程并开启新分组。
        """
        emit = log or (lambda message: None)
        cancelled = is_cancelled or (lambda: False)
        outcome = DecryptOutcome(source=task.source, success=False)

        self._enter_suffix_group(task.suffix)

        emit(f"正在读取源文件内存流: {task.source.name}")
        try:
            raw_bytes = self._reader.read(task.source)
        except Exception as exc:
            outcome.error = f"内存读取失败: {exc}"
            emit(outcome.error)
            return outcome
        emit(f"内存读取成功，文件大小: {len(raw_bytes)} 字节")

        process_queue = self._mapping.get_process_queue(task.suffix)
        for index, proc_name in enumerate(process_queue, 1):
            if cancelled():
                outcome.cancelled = True
                outcome.error = "已取消"
                return outcome
            emit(f"尝试第 {index} 个白名单进程 [{proc_name}.exe] 进行导出...")
            success, note = self._export_with_process(
                raw_bytes, proc_name, task.output, emit, in_place=task.in_place
            )
            outcome.attempts.append(f"{proc_name}: {note}")
            if success:
                outcome.success = True
                outcome.output = task.output
                outcome.process_name = proc_name
                outcome.sha256 = hashlib.sha256(raw_bytes).hexdigest()
                emit(f"[+] 校验通过！成功使用进程 [{proc_name}.exe] 导出无加密的明文文件!")
                emit(f"最终文件路径 : {task.output}")
                emit(f"文件 SHA256  : {outcome.sha256}")
                return outcome
            emit(f"进程 [{proc_name}.exe] 尝试落盘失败或未通过校验，尝试下一个备用进程...")

        outcome.error = "所有尝试的白名单进程均未能完成落盘（或导出文件仍呈密文特征）！"
        emit(outcome.error)
        return outcome

    # ------------------------------------------------------------------ 内部

    def _enter_suffix_group(self, suffix: str) -> None:
        """切换到新的后缀分组时，关闭上一分组的常驻伪装进程。"""
        if suffix == self._active_suffix:
            return
        self._close_sessions()
        self._active_suffix = suffix

    def _close_sessions(self) -> None:
        sessions, self._sessions = self._sessions, {}
        for session in sessions.values():
            session.close()

    def _export_with_process(
        self,
        raw_bytes: bytes,
        proc_name: str,
        final_file: Path,
        emit: LogCallback,
        in_place: bool = False,
    ) -> tuple[bool, str]:
        session = self._sessions.get(proc_name)
        if session is not None and not session.is_alive():
            session.close()
            session = None
        if session is None:
            try:
                writer_exe = self._writers.get_writer(proc_name)
            except Exception as exc:
                emit(f"打包伪装进程 [{proc_name}] 失败: {exc}")
                return False, f"构建失败: {exc}"
            try:
                session = _WriterSession(writer_exe)
            except Exception as exc:
                return False, f"伪装进程启动失败: {exc}"
            self._sessions[proc_name] = session

        pass_file = final_file.parent / f"{final_file.name}.pass"
        try:
            final_file.parent.mkdir(parents=True, exist_ok=True)
            pass_file.unlink(missing_ok=True)
            written, message = session.write_file(pass_file, raw_bytes)
        except Exception as exc:
            return False, f"落盘失败: {exc}"
        if not written:
            return False, f"落盘失败: {message}"

        if not pass_file.exists():
            return False, "伪装进程未产生输出文件"

        if in_place:
            # 原地覆盖：先校验临时文件为明文再替换原文件（重命名不改变内容），
            # 避免把密文临时文件覆盖到原始路径导致原文件损坏。
            if self._verifier.is_encrypted(pass_file) is True:
                emit("校验结果: 临时导出文件仍呈密文特征，原文件保持不变")
                try:
                    pass_file.unlink(missing_ok=True)
                except OSError:
                    pass
                return False, "导出仍为密文（原文件未改动）"
            if not self._move_into_place(pass_file, final_file):
                return False, "覆盖原文件失败"
            return True, "校验通过（原地覆盖）"

        if not self._move_into_place(pass_file, final_file):
            return False, "输出文件重命名失败"

        if self._verifier.is_encrypted(final_file) is True:
            emit(f"校验结果: 进程 [{proc_name}.exe] 导出的文件仍呈密文特征！")
            try:
                final_file.unlink(missing_ok=True)
            except OSError:
                pass
            return False, "输出仍为密文"
        return True, "校验通过"

    @staticmethod
    def _move_into_place(pass_file: Path, final_file: Path) -> bool:
        """把临时 ``*.pass`` 文件移动到目标路径（等价原脚本的 ``move /y``）。"""
        try:
            os.replace(pass_file, final_file)
            return final_file.exists()
        except OSError:
            # 与原始脚本一致的重命名兜底方案
            subprocess.run(
                f'cmd /c move /y "{pass_file}" "{final_file}"',
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW,
            )
            return final_file.exists() and not pass_file.exists()
