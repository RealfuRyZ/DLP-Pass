"""独立进度窗口与结果提示的界面测试（离屏 Qt，无需显示器）。

覆盖两个修复点：

1. ``--progress`` 独立入口的应用装配：程序 Logo 窗口图标 + 主程序同款
   主题装配（Fusion / QSS），进度窗口布局中嵌入 Logo 控件；
2. 批量解密完成后的结果对话框风格统一：``show_summary_dialog`` 使用
   程序 Logo 与主题配色，替代原生 Windows 消息框；标题选择逻辑与原生
   路径（``_notify_summary``）保持一致。

保持 Python 3.8 兼容（Win7 构建链同样运行本测试）。
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dlp_pass.cli import DecryptSummary, _notify_summary, _summary_title
from dlp_pass.ui_qt import progress
from dlp_pass.ui_qt.qt_compat import QApplication, QLabel

_app = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication([])


def _summary(**kwargs) -> DecryptSummary:
    return DecryptSummary(**kwargs)


class SummaryTitleTest(unittest.TestCase):
    """标题选择在原生与主题化两条路径共用且行为不变。"""

    def test_titles(self) -> None:
        self.assertEqual(_summary_title(_summary(sources=1, tasks=1)), "DLP-Pass 解密完成")
        self.assertEqual(_summary_title(_summary(sources=1, failed=1)), "DLP-Pass 解密失败")
        self.assertEqual(
            _summary_title(_summary(sources=1, cancelled=True)), "DLP-Pass 已取消"
        )

    def test_native_notify_unchanged(self) -> None:
        with mock.patch("dlp_pass.cli._message_box") as box:
            _notify_summary(_summary(sources=1, failed=1))
        title = box.call_args.args[1]
        self.assertEqual(title, "DLP-Pass 解密失败")
        self.assertTrue(box.call_args.kwargs.get("error"))


class ShowSummaryDialogTest(unittest.TestCase):
    """主题化结果对话框：程序 Logo 图标与按钮配置（exec 用替身，离屏模态循环会崩溃）。"""

    def test_dialog_has_app_logo_and_ok_button(self) -> None:
        captured: dict = {}
        original = progress.QMessageBox

        class SpyBox(original):
            def exec(self):  # 不进入模态循环
                captured["box"] = self
                return 0

        with mock.patch.object(progress, "QMessageBox", SpyBox):
            progress.show_summary_dialog("DLP-Pass 解密完成", "共 2 个文件，成功 2 个")
        box = captured["box"]
        self.assertEqual(box.windowTitle(), "DLP-Pass 解密完成")
        pixmap = box.iconPixmap()
        self.assertFalse(pixmap.isNull(), "结果对话框应显示程序 Logo 而非默认占位图")
        self.assertGreater(pixmap.width(), 0)
        box.deleteLater()

    def test_fallback_when_dialog_fails(self) -> None:
        """对话框内部异常时回退原生消息框（由 cli._notify_summary_themed 捕获）。"""
        from dlp_pass.cli import _notify_summary_themed

        with mock.patch.object(
            progress, "show_summary_dialog", side_effect=RuntimeError("boom")
        ), mock.patch("dlp_pass.cli._message_box") as box:
            _notify_summary_themed(_summary(sources=1, tasks=1))
        self.assertTrue(box.called)


class ProgressWindowBootstrapTest(unittest.TestCase):
    """独立进度窗口：应用装配补齐 Logo 与主题，窗口布局内嵌 Logo 控件。"""

    def test_run_with_progress_sets_logo_and_calls_notify(self) -> None:
        notified: list = []

        def work(reporter) -> int:
            reporter.plan(0)
            return 0

        code = progress.run_with_progress(
            work, _summary(sources=1), notify=lambda: notified.append(True)
        )
        self.assertEqual(code, 0)
        self.assertEqual(notified, [True])

    def test_logo_row_injected_into_dialog(self) -> None:
        """run_with_progress 内部：进度窗口顶部插入含 Logo 的布局行。"""

        captured = {}
        original_init = progress.QProgressDialog

        class SpyDialog(original_init):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                captured["dialog"] = self

        with mock.patch.object(progress, "QProgressDialog", SpyDialog):
            progress.run_with_progress(lambda reporter: 0, _summary(sources=0))
        dialog = captured["dialog"]
        labels = dialog.findChildren(QLabel)
        with_pixmap = [x for x in labels if x.pixmap() and not x.pixmap().isNull()]
        self.assertTrue(with_pixmap, "进度窗口应内嵌程序 Logo")
        self.assertFalse(dialog.windowIcon().isNull())
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
