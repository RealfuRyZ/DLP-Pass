"""打包运行环境辅助：识别打包产物并定位随包数据文件/目录。

发布版使用 PyInstaller onefile：运行时会把包内容解压到临时目录
（``sys._MEIPASS``），本模块提供统一的「随包资源」查找入口。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_bundled() -> bool:
    """当前是否运行于打包产物中（Python 源码直接运行时返回 False）。"""
    if getattr(sys, "frozen", False):       # PyInstaller
        return True
    if getattr(sys, "_MEIPASS", None):      # PyInstaller onefile 解压目录
        return True
    return "__compiled__" in globals()      # 其他编译器（预留）


def _candidate_bases() -> list[Path]:
    bases: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(Path(meipass))
    bases.append(Path(sys.executable).resolve().parent)
    try:
        # 打包产物中：.../dlp_pass/core/bundle.py 上溯 2 层 = 解压目录根
        bases.append(Path(__file__).resolve().parents[2])
    except (IndexError, OSError):
        pass
    return bases


def find_data_file(name: str) -> Path | None:
    """在打包产物常见位置查找随包数据文件（如 writer_stub.exe）。

    查找顺序：onefile 解压目录 -> 可执行文件同目录 -> 包内相对位置。
    """
    for base in _candidate_bases():
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


def find_data_dir(name: str) -> Path | None:
    """在打包产物常见位置查找随包数据目录（如 mappings/）。"""
    for base in _candidate_bases():
        candidate = base / name
        if candidate.is_dir():
            return candidate
    return None
