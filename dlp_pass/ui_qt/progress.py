"""右键菜单解密用的独立进度窗口（不打开主界面）。

流程：首个进程汇总完多选路径后创建一个小型 ``QProgressDialog``；
扫描阶段显示不确定进度，解密阶段显示 ``已完成 / 总数``，用户可随时取消。
解密在后台 Python 线程中执行，通过 Qt 信号回到界面线程更新进度。

外观与主程序一致：本模块负责补齐独立入口的应用装配（AppUserModelID、
Fusion 风格、持久化主题 QSS、标题栏程序 Logo 图标——进度窗口内容区不内嵌
Logo），并提供主题化的结果提示 :func:`show_summary_dialog`（替代原生 Windows
消息框）。
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from typing import Callable

from . import settings, theme
from .assets import app_icon, logo_pixmap
from .qt_compat import (
    QApplication,
    QFont,
    QMessageBox,
    QObject,
    QProgressDialog,
    Qt,
    prepare_high_dpi,
    pyqtSignal,
)

_logger = logging.getLogger(__name__)

_APP_USER_MODEL_ID = "RealfuRyZ.DLPPass"


def _bootstrap_app() -> QApplication:
    """创建（或复用）与主程序外观一致的全局 QApplication。"""
    app = QApplication.instance()
    if app is not None:
        return app
    if sys.platform == "win32":
        try:
            # 让 Windows 任务栏按应用而不是 python.exe 分组并显示自定义图标
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_APP_USER_MODEL_ID)
        except Exception:
            _logger.debug("设置 AppUserModelID 失败", exc_info=True)
    prepare_high_dpi()
    app = QApplication([sys.argv[0] if sys.argv else "DLP-Pass"])
    app.setApplicationName("DLP-Pass")
    app.setStyle("Fusion")
    theme.set_active_theme(settings.load_theme())  # 恢复上次使用的配色（默认暗色）
    app.setFont(QFont(theme.FONT_FAMILY, 10))
    app.setStyleSheet(theme.build_stylesheet())
    app.setWindowIcon(app_icon())
    return app


class _ProgressReporter(QObject):
    """后台线程 -> Qt 主线程的进度信号；同时实现 CLI 的进度回调接口。"""

    scan_progress = pyqtSignal(int, int)
    planned = pyqtSignal(int)
    file_started = pyqtSignal(int, int, str)
    file_finished = pyqtSignal(int, int, bool)
    finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._cancel = threading.Event()

    def scan(self, scanned: int, candidates: int) -> None:
        self.scan_progress.emit(scanned, candidates)

    def plan(self, total: int) -> None:
        self.planned.emit(total)

    def start_file(self, index: int, total: int, name: str) -> None:
        self.file_started.emit(index, total, name)

    def finish_file(self, index: int, total: int, success: bool) -> None:
        self.file_finished.emit(index, total, success)

    def should_cancel(self) -> bool:
        return self._cancel.is_set()

    def request_cancel(self) -> None:
        self._cancel.set()


def show_summary_dialog(title: str, text: str, *, success: bool = True) -> None:
    """与主程序风格一致的批量解密结果提示（程序 Logo + 主题配色）。

    须在 :func:`_bootstrap_app` 完成后、于界面线程调用；模态阻塞直至关闭。
    """
    box = QMessageBox()
    box.setWindowTitle(title)
    box.setIconPixmap(logo_pixmap(44))
    box.setText(title)
    box.setInformativeText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.setDefaultButton(QMessageBox.StandardButton.Ok)
    # 仅标题用状态强调色（成功/失败），正文保持主题中性色，与主界面一致
    accent = theme.COLORS["success" if success else "danger"]
    box.setStyleSheet(
        "QMessageBox QLabel#qt_msgbox_label { color: %s; font-weight: 600; }" % accent
    )
    box.exec()


def run_with_progress(
    work: Callable[[_ProgressReporter], int],
    summary,
    notify: Callable[[], None] | None = None,
) -> int:
    """创建进度窗口并执行 ``work``；返回 ``work`` 的退出码。

    ``work`` 在后台线程运行，必须只通过 ``reporter`` 的方法与界面交互。
    ``notify`` 不为空时，在任务结束、窗口关闭后（界面线程）调用一次，
    用于展示与主程序风格一致的结果提示。
    """
    app = _bootstrap_app()
    app.setQuitOnLastWindowClosed(False)

    dialog = QProgressDialog("正在扫描文件…", "取消", 0, 0)
    dialog.setWindowTitle("DLP-Pass 解密")
    dialog.setWindowIcon(app_icon())  # 仅标题栏左侧使用程序 Logo
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.setMinimumWidth(460)
    dialog.setValue(0)

    reporter = _ProgressReporter()

    def on_scan(scanned: int, candidates: int) -> None:
        dialog.setRange(0, 0)
        dialog.setLabelText(f"正在扫描文件… 已扫描 {scanned} 个，待解密 {candidates} 个")

    def on_planned(total: int) -> None:
        if total <= 0:
            dialog.setRange(0, 1)
            dialog.setValue(1)
            dialog.setLabelText("没有需要解密的文件")
        else:
            dialog.setRange(0, total)
            dialog.setValue(0)
            dialog.setLabelText(f"正在解密 0/{total}…")

    def on_file_started(index: int, total: int, name: str) -> None:
        dialog.setLabelText(f"正在解密 {index}/{total}：{name}")

    def on_file_finished(done: int, total: int, success: bool) -> None:
        dialog.setValue(done)

    def on_cancel() -> None:
        reporter.request_cancel()
        dialog.setLabelText("正在取消…")
        dialog.setCancelButton(None)

    def on_finished() -> None:
        dialog.close()
        if notify is not None:
            try:
                notify()
            except Exception:  # 结果提示失败不影响退出码与日志落盘
                _logger.exception("展示结果提示失败")
        app.quit()

    reporter.scan_progress.connect(on_scan)
    reporter.planned.connect(on_planned)
    reporter.file_started.connect(on_file_started)
    reporter.file_finished.connect(on_file_finished)
    reporter.finished.connect(on_finished)
    dialog.canceled.connect(on_cancel)
    dialog.rejected.connect(on_cancel)

    result = {"code": 1}

    def worker() -> None:
        try:
            result["code"] = int(work(reporter))
        except Exception as exc:
            _logger.exception("进度窗口任务异常")
            summary.fatal_error = f"内部错误: {exc}"
            result["code"] = 1
        finally:
            reporter.finished.emit()

    dialog.show()
    thread = threading.Thread(target=worker, name="dlp-pass-decrypt", daemon=True)
    thread.start()
    app.exec()
    thread.join(timeout=5)
    return result["code"]
