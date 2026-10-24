"""应用图标与 Logo 资源定位（源码运行 / PyInstaller onedir 通用）。"""

from __future__ import annotations

from pathlib import Path

from ..core.bundle import find_data_file
from .qt_compat import QIcon, QPixmap


def logo_path() -> Path | None:
    """返回随包 PNG Logo 路径；缺失时返回 ``None``。"""
    return find_data_file("assets/logo.png")


def app_icon() -> QIcon:
    """构造应用图标（用于标题栏、任务栏与 QApplication）。"""
    path = logo_path()
    if path is None:
        return QIcon()
    return QIcon(str(path))


def logo_pixmap(size: int) -> QPixmap:
    """返回指定边长的 Logo 位图；资源缺失时返回空 QPixmap。"""
    icon = app_icon()
    if icon.isNull():
        return QPixmap()
    return icon.pixmap(size, size)
