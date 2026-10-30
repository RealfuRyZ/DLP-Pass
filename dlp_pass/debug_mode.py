"""调试模式：解密流程详细日志 + 中间过程文件留档。

开启后（由界面顶栏「调试模式」按钮或启动时恢复的偏好触发）：

1. 根日志器提升到 DEBUG 并额外写入 ``<日志目录>/dlp_pass_debug.log``
   （见 :func:`dlp_pass.logging_setup.set_debug_logging`），解密流程各关键
   步骤（映射加载 / 源读取 / 委派读取 / 密文判定 / 伪装进程落盘 / 校验）
   输出详细日志；
2. 尝试解密过程中的所有中间文件（磁盘原始字节、委派读取字节、临时
   ``*.pass`` 文件的磁盘回读内容、文件头窗口、映射文件原始副本等）写入
   ``<日志目录>/debug/<时间戳>/`` 留档，便于远程排查
   （如 issue #1：怀疑映射规则文件本身被 DLP 驱动加密）。

本模块只管理运行期状态与留档；偏好持久化在界面层
``dlp_pass.ui_qt.settings``，核心模块通过 :func:`is_debug_enabled` 与
:func:`save_bytes` 接入，不依赖 Qt。保持 Python 3.8 兼容。
"""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from . import logging_setup

_logger = logging.getLogger(__name__)

_LOCK = __import__("threading").RLock()
_ENABLED = False
_RUN_DIR: "Path | None" = None

# 头部预览字节数（写日志用的十六进制串长度）
HEAD_PREVIEW_BYTES = 64

_UNSAFE_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def is_debug_enabled() -> bool:
    """当前是否处于调试模式。"""
    return _ENABLED


def debug_dir() -> "Path | None":
    """本次调试会话的中间文件留档目录（未开启时为 ``None``）。"""
    with _LOCK:
        return _RUN_DIR


def set_debug_enabled(enabled: bool) -> "Path | None":
    """切换全局调试模式。

    返回本次会话的中间文件留档目录（关闭时返回 ``None``）；
    重复设置同一状态为幂等操作（不会新建留档目录）。
    """
    global _ENABLED, _RUN_DIR
    with _LOCK:
        if enabled == _ENABLED:
            return _RUN_DIR if enabled else None
        _ENABLED = enabled
        debug_log_path = logging_setup.set_debug_logging(enabled)
        if enabled:
            _RUN_DIR = _make_run_dir()
            _log_session_environment(debug_log_path)
        else:
            _RUN_DIR = None
        return _RUN_DIR


def head_preview(data: bytes, size: int = HEAD_PREVIEW_BYTES) -> str:
    """前 ``size`` 字节的十六进制预览（空数据返回 ``(空)``）。"""
    if not data:
        return "(空)"
    return data[:size].hex().upper()


def save_bytes(name: str, data: bytes) -> "Path | None":
    """调试模式下把中间文件字节写入留档目录并返回路径；未开启或写入失败返回 ``None``。

    同名文件自动追加序号避免覆盖；``name`` 中的路径分隔符与 Windows
    非法字符会被替换为下划线。
    """
    if not _ENABLED:
        return None
    with _LOCK:
        run_dir = _RUN_DIR
    if run_dir is None:
        return None
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        target = _unique_path(run_dir / _safe_name(name))
        target.write_bytes(data)
        _logger.debug("调试留档: 中间文件已保存 %s（%d 字节）", target, len(data))
        return target
    except OSError as exc:
        _logger.debug("调试留档: 中间文件写入失败 %s: %s", name, exc)
        return None


def save_copy(source: Path, name: "str | None" = None) -> "Path | None":
    """把磁盘文件按当前进程的原始视角复制进留档目录（读到什么存什么）。

    用于核对「文件是否被 DLP 驱动转加密」：非白名单进程读到的即磁盘
    原始密文，若与伪装进程写入的明文不一致即可定位问题。
    """
    if not _ENABLED:
        return None
    try:
        data = Path(source).read_bytes()
    except OSError as exc:
        _logger.debug("调试留档: 读取源文件失败 %s: %s", source, exc)
        return None
    return save_bytes(name or f"{Path(source).name}.copy.bin", data)


# ---------------------------------------------------------------------- 内部


def _safe_name(name: str) -> str:
    cleaned = _UNSAFE_NAME_RE.sub("_", str(name)).strip() or "debug-file"
    return cleaned[:120]


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}.{index}{suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{stem}.{os.getpid()}{suffix}")


def _make_run_dir() -> "Path | None":
    """创建本次会话的留档目录 ``<日志目录>/debug/<时间戳>/``。"""
    base = logging_setup.resolve_log_dir()
    if base is None:
        _logger.debug("调试留档: 未找到可用日志目录，中间文件不会留档")
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    try:
        run_dir = base / "debug" / stamp
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir
    except OSError as exc:
        _logger.debug("调试留档: 创建留档目录失败: %s", exc)
        return None


def _log_session_environment(debug_log_path: "Path | None") -> None:
    """会话开始时记录运行环境，便于远程比对不同机器的差异。"""
    _logger.debug(
        "===== 调试模式开启 ===== 版本=%s 冻结=%s Python=%s 可执行文件=%s",
        _app_version(), getattr(sys, "frozen", False), sys.version.split()[0], sys.executable,
    )
    _logger.debug("详细日志文件: %s", debug_log_path if debug_log_path else "（未启用文件输出）")
    with _LOCK:
        run_dir = _RUN_DIR
    _logger.debug("中间文件留档目录: %s", run_dir)


def _app_version() -> str:
    try:
        import dlp_pass

        return dlp_pass.__version__
    except Exception:
        return "?"
