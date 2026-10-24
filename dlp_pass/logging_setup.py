"""日志初始化（图形界面与命令行共用）。

有控制台时同时输出到控制台。日志文件优先写入程序目录下的 ``logs/``：
- 安装版（PyInstaller onedir）：``<安装目录>\\logs\\dlp_pass.log``；
- 源码运行：``<项目根目录>\\logs\\dlp_pass.log``；
- 程序目录不可写时回退到 ``%LOCALAPPDATA%\\DLP-Pass\\logs\\dlp_pass.log``。
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterator


def _log_dirs() -> Iterator[Path]:
    """按优先级返回候选日志目录（先程序目录，后用户本地目录）。"""
    if getattr(sys, "frozen", False):
        yield Path(sys.executable).resolve().parent / "logs"
    else:
        yield Path(__file__).resolve().parents[1] / "logs"
    yield Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DLP-Pass" / "logs"


def setup_logging() -> Path | None:
    """初始化根日志记录器，返回实际使用的日志文件路径（全部失败返回 ``None``）。"""
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list[logging.Handler] = []

    if sys.stderr is not None:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
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
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)
            log_path = candidate
            break
        except OSError:
            continue

    logging.basicConfig(level=logging.INFO, handlers=handlers)
    return log_path
