"""界面暗色 / 亮色主题切换测试（离屏 Qt，无需显示器）。

覆盖需求约定：
- 顶栏「映射规则版本号」徽标左侧存在主题切换按钮，文本显示当前模式；
- 点击后全局配色切换（QSS 重建、内联颜色刷新），两套调色板键集合一致；
- 偏好持久化到 settings.json，重启装配（load_theme -> set_active_theme）能恢复；
- 主题切换同时刷新版本号的按状态着色与悬浮提示。

保持 Python 3.8 兼容（Win7 构建链同样运行本测试）。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import dlp_pass
from dlp_pass.config import AppConfig
from dlp_pass.core.mapping_loader import MappingSet
from dlp_pass.ui_qt import main_window as mw
from dlp_pass.ui_qt import settings, theme
from dlp_pass.ui_qt.qt_compat import QApplication

_app = None
_tmp_dir: Path | None = None
_env_patch = None


def setUpModule() -> None:
    global _app, _tmp_dir, _env_patch
    _app = QApplication.instance() or QApplication([])
    _tmp_dir = Path(tempfile.mkdtemp(prefix="dlp-theme-test-"))
    _env_patch = mock.patch.dict(os.environ, {"DLP_PASS_SETTINGS_DIR": str(_tmp_dir)})
    _env_patch.start()
    theme.set_active_theme("dark")
    QApplication.instance().setStyleSheet(theme.build_stylesheet())


def tearDownModule() -> None:
    if _env_patch is not None:
        _env_patch.stop()
    if _tmp_dir is not None:
        shutil.rmtree(_tmp_dir, ignore_errors=True)
    theme.set_active_theme("dark")


class ThemeModelTest(unittest.TestCase):
    def test_palettes_have_identical_keys(self) -> None:
        self.assertEqual(set(theme.DARK_COLORS), set(theme.LIGHT_COLORS))

    def test_stylesheets_use_respective_palettes(self) -> None:
        theme.set_active_theme("dark")
        dark_sheet = theme.build_stylesheet()
        theme.set_active_theme("light")
        light_sheet = theme.build_stylesheet()
        self.assertIn(theme.DARK_COLORS["window"], dark_sheet)
        self.assertNotIn(theme.DARK_COLORS["window"], light_sheet)
        self.assertIn(theme.LIGHT_COLORS["window"], light_sheet)
        self.assertNotIn("$", light_sheet)  # 不允许残留未替换的模板变量
        self.assertNotIn("$", dark_sheet)

    def test_state_colors_follow_active_palette(self) -> None:
        theme.set_active_theme("light")
        self.assertEqual(theme.STATE_COLORS["danger"], theme.LIGHT_COLORS["danger"])
        theme.set_active_theme("dark")
        self.assertEqual(theme.STATE_COLORS["danger"], theme.DARK_COLORS["danger"])


class SettingsPersistenceTest(unittest.TestCase):
    def test_default_and_roundtrip(self) -> None:
        self.assertEqual(settings.load_theme(), "dark")  # 无文件 -> 默认暗色
        self.assertTrue(settings.save_theme("light"))
        self.assertEqual(settings.load_theme(), "light")
        self.assertTrue(settings.save_theme("dark"))
        self.assertEqual(settings.load_theme(), "dark")

    def test_invalid_value_falls_back_to_default(self) -> None:
        settings.save_theme("dark")
        path = settings.settings_path()
        data = settings._read_all()
        data["theme"] = "neon"
        import json

        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(settings.load_theme(), "dark")


class ThemeToggleUiTest(unittest.TestCase):
    def setUp(self) -> None:
        theme.set_active_theme("dark")
        QApplication.instance().setStyleSheet(theme.build_stylesheet())
        self.window = mw.MainWindow(AppConfig(check_updates=False), MappingSet("1.0"))
        self.addCleanup(self.window._verifier.close)

    def tearDown(self) -> None:
        theme.set_active_theme("dark")
        QApplication.instance().setStyleSheet(theme.build_stylesheet())

    def test_button_exists_left_of_mapping_badge_and_shows_current_mode(self) -> None:
        self.assertEqual(self.window._theme_button.text(), "暗色模式")
        self.assertIn("亮色", self.window._theme_button.toolTip())
        # 按钮在顶栏中位于映射规则徽标之前（左侧）
        bar = self.window._theme_button.parentWidget()
        index_button = bar.layout().indexOf(self.window._theme_button)
        index_badge = bar.layout().indexOf(self.window._mapping_badge)
        self.assertGreaterEqual(index_button, 0)
        self.assertLess(index_button, index_badge)

    def test_toggle_switches_palette_applies_stylesheet_and_persists(self) -> None:
        self.window._on_theme_toggled()
        self.assertEqual(theme.active_theme(), "light")
        self.assertEqual(self.window._theme_button.text(), "亮色模式")
        sheet = QApplication.instance().styleSheet()
        self.assertIn(theme.LIGHT_COLORS["window"], sheet)
        self.assertIn(theme.LIGHT_COLORS["text"], sheet)
        self.assertEqual(settings.load_theme(), "light")

        self.window._on_theme_toggled()
        self.assertEqual(theme.active_theme(), "dark")
        self.assertEqual(settings.load_theme(), "dark")
        self.assertIn(theme.DARK_COLORS["window"], QApplication.instance().styleSheet())

    def test_startup_restores_saved_preference(self) -> None:
        settings.save_theme("light")
        theme.set_active_theme("light")  # 模拟 app.run() 装配顺序：先恢复再建窗
        QApplication.instance().setStyleSheet(theme.build_stylesheet())
        window = mw.MainWindow(AppConfig(check_updates=False), MappingSet("1.0"))
        self.addCleanup(window._verifier.close)
        self.assertEqual(window._theme_button.text(), "亮色模式")
        self.assertIn(theme.LIGHT_COLORS["success"],
                      (window._stat_success._value.styleSheet()))

    def test_toggle_recolors_version_label_per_check_state(self) -> None:
        self.window._on_version_checked("v99.0.0")
        self.assertIn(theme.DARK_COLORS["danger"], self.window._version_label.styleSheet())
        self.window._on_theme_toggled()
        self.assertIn(theme.LIGHT_COLORS["danger"], self.window._version_label.styleSheet())
        self.assertEqual(self.window._version_label.toolTip(), "有新版本可用 v99.0.0")
        self.window._on_version_checked(dlp_pass.__version__)
        self.assertIn(theme.LIGHT_COLORS["success"], self.window._version_label.styleSheet())
        self.assertEqual(self.window._version_label.toolTip(), "当前为最新版本")


if __name__ == "__main__":
    unittest.main()
