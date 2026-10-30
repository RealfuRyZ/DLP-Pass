"""日志初始化（图形界面与命令行共用）。

有控制台时同时输出到控制台。日志文件优先写入程序目录下的 ``logs/``：
- 安装版（PyInstaller onedir）：``<安装目录>\\logs\\dlp_pass.log``；
- 源码运行：``<项目根目录>\\logs\\dlp_pass.log``；
- 程序目录不可写时回退到 ``%LOCALAPPDATA%\\DLP-Pass\\logs\\dlp_pass.log``；
- 环境变量 ``DLP_PASS_LOG_DIR`` 可强制指定日志目录（优先级最高，便于测试）。

调试模式（见 :func:`set_debug_logging`）：根日志器提升到 DEBUG，并在日志
目录下额外生成 ``dlp_pass_debug.log`` 详细日志文件；关闭后自动移除该文件
处理器并恢复 INFO 级别。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterator

LOG_DIR_ENV = "DLP_PASS_LOG_DIR"
DEBUG_LOG_FILENAME = "dlp_pass_debug.log"

_formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

_selected_log_dir: Path | None = None
_debug_lock = threading.Lock()
_debug_handler: logging.Handler | None = None


def _log_dirs() -> Iterator[Path]:
    """按优先级返回候选日志目录（环境变量 -> 程序目录 -> 用户本地目录）。"""
    override = os.environ.get(LOG_DIR_ENV, "").strip()
    if override:
        yield Path(override)
    if getattr(sys, "frozen", False):
        yield Path(sys.executable).resolve().parent / "logs"
    else:
        yield Path(__file__).resolve().parents[1] / "logs"
    yield Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DLP-Pass" / "logs"


def setup_logging() -> Path | None:
    """初始化根日志记录器，返回实际使用的日志文件路径（全部失败返回 ``None``）。"""
    global _selected_log_dir
    handlers: list[logging.Handler] = []

    if sys.stderr is not None:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(_formatter)
        handlers.append(stream_handler)

    log_path: Path | None = None
    seen: set[str] = set()
    for log_dir in _log_dirs():
        key = os.path.normcase(str(log_dir))
        if key in seen:
            continue
        seen.add(key)
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            candidate = log_dir / "dlp_pass.log"
            file_handler = RotatingFileHandler(
                candidate, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(_formatter)
            handlers.append(file_handler)
            log_path = candidate
            _selected_log_dir = log_dir
            break
        except OSError:
            continue

    logging.basicConfig(level=logging.INFO, handlers=handlers)
    return log_path


def get_log_dir() -> Path | None:
    """``setup_logging`` 实际选定的日志目录（未初始化 / 全部失败时为 ``None``）。"""
    return _selected_log_dir


def resolve_log_dir() -> Path | None:
    """返回首个可用的日志目录：环境变量优先，其次已选定目录，再按候选顺序探测。"""
    override = os.environ.get(LOG_DIR_ENV, "").strip()
    if override:
        path = Path(override)
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            pass
    if _selected_log_dir is not None:
        return _selected_log_dir
    for log_dir in _log_dirs():
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            if os.access(log_dir, os.W_OK):
                return log_dir
        except OSError:
            continue
    return None


def set_debug_logging(enabled: bool) -> Path | None:
    """开启 / 关闭调试日志。

    开启：根日志器级别提升到 DEBUG，并挂载 ``<日志目录>/dlp_pass_debug.log``
    滚动文件处理器（DEBUG 级，含全部解密流程详细日志），返回该文件路径；
    日志目录不可用时仅提升级别并返回 ``None``。
    关闭：移除调试文件处理器并恢复 INFO 级别，返回 ``None``。可重复调用。
    """
    global _debug_handler
    with _debug_lock:
        root = logging.getLogger()
        if enabled:
            root.setLevel(logging.DEBUG)
            if _debug_handler is not None:
                return Path(_debug_handler.baseFilename)  # type: ignore[arg-type]
            log_dir = resolve_log_dir()
            if log_dir is None:
                return None
            try:
                handler = RotatingFileHandler(
                    log_dir / DEBUG_LOG_FILENAME,
                    maxBytes=5_000_000,
                    backupCount=3,
                    encoding="utf-8",
                )
            except OSError:
                return None
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(_formatter)
            root.addHandler(handler)
            _debug_handler = handler
            return log_dir / DEBUG_LOG_FILENAME
        root.setLevel(logging.INFO)
        if _debug_handler is not None:
            root.removeHandler(_debug_handler)
            try:
                _debug_handler.close()
            except Exception:
                pass
            _debug_handler = None
        return None
