"""应用入口：初始化 QApplication、暗色/亮色主题与主窗口（PyQt6 / PyQt5 双兼容）。"""

from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path

import dlp_pass
from ..config import load_runtime_config
from ..core.mapping_loader import load_latest
from ..logging_setup import setup_logging
from . import settings, theme
from .assets import app_icon
from .main_window import MainWindow
from .qt_compat import QApplication, QFont, QMessageBox, prepare_high_dpi

_logger = logging.getLogger(__name__)


def _install_exception_hook() -> None:
    """未捕获异常时记录日志并弹出提示，避免界面无声崩溃。"""

    def hook(exc_type, exc_value, exc_traceback):
        _logger.critical("未捕获异常", exc_info=(exc_type, exc_value, exc_traceback))
        try:
            QMessageBox.critical(
                None,
                "发生未处理的异常",
                f"{exc_type.__name__}: {exc_value}\n\n详细信息已写入日志。",
            )
        except Exception:
            pass

    sys.excepthook = hook


def run() -> None:
    """程序入口：加载配置与映射后启动 Qt 图形界面。"""
    setup_logging()

    if sys.platform == "win32":
        try:
            # 让 Windows 任务栏按应用而不是 python.exe 分组并显示自定义图标
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "RealfuRyZ.DLPPass"
            )
        except Exception:
            _logger.debug("设置 AppUserModelID 失败", exc_info=True)

    prepare_high_dpi()
    app = QApplication(sys.argv)
    app.setApplicationName("DLP 解密助手")
    app.setApplicationVersion(dlp_pass.__version__)
    app.setWindowIcon(app_icon())
    app.setStyle("Fusion")
    theme.set_active_theme(settings.load_theme())  # 恢复上次使用的配色（默认暗色）
    app.setFont(QFont(theme.FONT_FAMILY, 10))
    app.setStyleSheet(theme.build_stylesheet())

    _install_exception_hook()

    project_root = Path(__file__).resolve().parents[2]
    config = load_runtime_config(project_root)
    mapping = load_latest(config, project_root)  # 启动约定：读取 mappings/ 中最新版本
    window = MainWindow(config, mapping)
    window.show()
    sys.exit(app.exec())
