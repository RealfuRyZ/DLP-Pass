"""主界面 Logo 与版本状态展示测试（离屏 Qt）。"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import dlp_pass
from dlp_pass.config import AppConfig
from dlp_pass.core.mapping_loader import MappingSet
from dlp_pass.ui_qt import main_window as mw
from dlp_pass.ui_qt import theme
from dlp_pass.ui_qt.qt_compat import QApplication
from dlp_pass.version_check import RELEASE_PAGE_URL

_app = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication([])


class VersionUiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.window = mw.MainWindow(AppConfig(check_updates=False), MappingSet("1.0"))
        self.addCleanup(self.window._verifier.close)
        self.addCleanup(self.window.close)

    def test_logo_and_initial_version(self) -> None:
        self.assertFalse(self.window.windowIcon().isNull())
        self.assertEqual(self.window._version_label.text(), f"v{dlp_pass.__version__}")
        self.assertTrue(self.window._update_hint.isHidden())

    def test_latest_version_is_green_without_hint(self) -> None:
        self.window._on_version_checked(dlp_pass.__version__)
        self.assertIn(theme.COLORS["success"], self.window._version_label.styleSheet())
        self.assertTrue(self.window._update_hint.isHidden())

    def test_newer_version_is_red_and_clickable(self) -> None:
        self.window._on_version_checked("v99.0.0")
        self.assertIn(theme.COLORS["danger"], self.window._version_label.styleSheet())
        self.assertFalse(self.window._update_hint.isHidden())
        text = self.window._update_hint.text()
        self.assertIn("有新版本可用", text)
        self.assertIn(RELEASE_PAGE_URL, text)

    def test_failed_check_restores_default_appearance(self) -> None:
        self.window._on_version_checked("v99.0.0")
        self.window._on_version_check_failed()
        self.assertEqual(self.window._version_label.styleSheet(), "")
        self.assertTrue(self.window._update_hint.isHidden())


if __name__ == "__main__":
    unittest.main()
