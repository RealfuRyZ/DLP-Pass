"""调试模式测试：开关状态、详细日志、中间文件留档、偏好持久化与界面交互（离屏 Qt）。

覆盖需求约定：
- 顶栏主题切换按钮左侧存在可勾选的「调试模式」按钮；
- 开启后根日志器提升到 DEBUG 并在日志目录额外生成 dlp_pass_debug.log；
- 解密流程中间文件（源文件磁盘字节、映射文件原始副本等）留档到日志目录；
- 首次开启弹出磁盘占用提示（仅一次），开关状态持久化并在启动时恢复。

保持 Python 3.8 兼容（Win7 构建链同样运行本测试）。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dlp_pass import debug_mode, logging_setup
from dlp_pass.config import AppConfig
from dlp_pass.core import mapping_loader
from dlp_pass.core.mapping_loader import MappingSet, debug_snapshot, load_latest
from dlp_pass.core.source_reader import SourceReader
from dlp_pass.ui_qt import main_window as mw
from dlp_pass.ui_qt import settings
from dlp_pass.ui_qt.qt_compat import QApplication

_app = None
_tmp_root: Path | None = None
_env_patch = None


def setUpModule() -> None:
    global _app, _tmp_root, _env_patch
    _app = QApplication.instance() or QApplication([])
    _tmp_root = Path(tempfile.mkdtemp(prefix="dlp-debug-test-"))
    _env_patch = mock.patch.dict(
        os.environ,
        {
            "DLP_PASS_SETTINGS_DIR": str(_tmp_root / "settings"),
            "DLP_PASS_LOG_DIR": str(_tmp_root / "logs"),
        },
    )
    _env_patch.start()


def tearDownModule() -> None:
    debug_mode.set_debug_enabled(False)
    if _env_patch is not None:
        _env_patch.stop()
    if _tmp_root is not None:
        shutil.rmtree(_tmp_root, ignore_errors=True)


class DebugModeCoreTest(unittest.TestCase):
    """debug_mode 开关、留档目录与 logging_setup 联动。"""

    def tearDown(self) -> None:
        debug_mode.set_debug_enabled(False)

    def test_disabled_by_default(self) -> None:
        self.assertFalse(debug_mode.is_debug_enabled())
        self.assertIsNone(debug_mode.debug_dir())
        self.assertIsNone(debug_mode.save_bytes("x.bin", b"data"))

    def test_enable_raises_level_creates_debug_log_and_run_dir(self) -> None:
        run_dir = debug_mode.set_debug_enabled(True)
        self.assertTrue(debug_mode.is_debug_enabled())
        self.assertIsNotNone(run_dir)
        self.assertTrue(run_dir.is_dir())
        logs_dir = Path(os.environ["DLP_PASS_LOG_DIR"])
        self.assertEqual(run_dir.parent.parent, logs_dir)
        self.assertEqual(logging.getLogger().level, logging.DEBUG)
        debug_log = logs_dir / logging_setup.DEBUG_LOG_FILENAME
        self.assertTrue(debug_log.is_file())

    def test_enable_is_idempotent_and_saves_files(self) -> None:
        first = debug_mode.set_debug_enabled(True)
        second = debug_mode.set_debug_enabled(True)
        self.assertEqual(first, second)
        saved = debug_mode.save_bytes("sample.bin", b"hello")
        self.assertIsNotNone(saved)
        self.assertEqual(saved.read_bytes(), b"hello")
        again = debug_mode.save_bytes("sample.bin", b"world")
        self.assertNotEqual(saved, again)
        self.assertTrue(again.is_file())

    def test_save_bytes_sanitizes_name(self) -> None:
        run_dir = debug_mode.set_debug_enabled(True)
        saved = debug_mode.save_bytes(r"sub\dir:bad?name*.bin", b"x")
        self.assertIsNotNone(saved)
        self.assertEqual(saved.parent, run_dir)
        self.assertNotIn(":", saved.name)

    def test_disable_restores_level_and_stops_saving(self) -> None:
        debug_mode.set_debug_enabled(True)
        self.assertIs(debug_mode.set_debug_enabled(False), None)
        self.assertFalse(debug_mode.is_debug_enabled())
        self.assertIsNone(debug_mode.debug_dir())
        self.assertIsNone(debug_mode.save_bytes("x.bin", b"data"))
        self.assertEqual(logging.getLogger().level, logging.INFO)

    def test_head_preview(self) -> None:
        self.assertEqual(debug_mode.head_preview(b"\xd0\xcf\x11\xe0"), "D0CF11E0")
        self.assertEqual(debug_mode.head_preview(b""), "(空)")


class SettingsDebugPersistenceTest(unittest.TestCase):
    """调试模式偏好与首次提示标记的持久化。"""

    def setUp(self) -> None:
        path = settings.settings_path()
        if path.exists():
            path.unlink()

    def tearDown(self) -> None:
        path = settings.settings_path()
        if path.exists():
            path.unlink()

    def test_defaults(self) -> None:
        self.assertFalse(settings.load_debug_mode())
        self.assertFalse(settings.load_debug_notice_shown())

    def test_roundtrip(self) -> None:
        self.assertTrue(settings.save_debug_mode(True))
        self.assertTrue(settings.load_debug_mode())
        self.assertTrue(settings.save_debug_mode(False))
        self.assertFalse(settings.load_debug_mode())
        self.assertTrue(settings.save_debug_notice_shown())
        self.assertTrue(settings.load_debug_notice_shown())

    def test_string_values_are_coerced(self) -> None:
        settings.settings_path().parent.mkdir(parents=True, exist_ok=True)
        settings.settings_path().write_text(
            json.dumps({"debug_mode": "true", "debug_notice_shown": "no"}), encoding="utf-8"
        )
        self.assertTrue(settings.load_debug_mode())
        self.assertFalse(settings.load_debug_notice_shown())


class MappingDebugTest(unittest.TestCase):
    """映射加载与调试快照：原始字节留档 + 「是否被转加密」判定。"""

    def setUp(self) -> None:
        debug_mode.set_debug_enabled(False)
        self.workdir = Path(tempfile.mkdtemp(prefix="dlp-debug-map-"))
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.addCleanup(debug_mode.set_debug_enabled, False)

    def test_load_latest_archives_raw_mapping_files(self) -> None:
        mappings = self.workdir / "mappings"
        mappings.mkdir()
        valid = {"version": "20260101.1", "rules": [
            {"extension": ".doc", "primary": "winword", "alternatives": ["wps"]},
        ]}
        (mappings / "20260101.1.json").write_text(json.dumps(valid), encoding="utf-8")
        (mappings / "20260102.1.json").write_bytes(b"\x90\xf8\xa4\x2b" + os.urandom(64))
        debug_mode.set_debug_enabled(True)
        mapping = load_latest(project_root=self.workdir)
        self.assertEqual(mapping.version, "20260101.1")  # 损坏文件被跳过，取有效最新版
        run_dir = debug_mode.debug_dir()
        names = {p.name for p in run_dir.iterdir()}
        self.assertIn("mapping-20260101.1.json.raw.bin", names)
        self.assertIn("mapping-20260102.1.json.raw.bin", names)

    def test_debug_snapshot_copies_active_mapping(self) -> None:
        source = self.workdir / "20260101.1.json"
        source.write_text(json.dumps({"rules": []}), encoding="utf-8")
        mapping = MappingSet("20260101.1", {}, source=source)
        debug_mode.set_debug_enabled(True)
        debug_snapshot(mapping)
        saved = list(debug_mode.debug_dir().glob("mapping-active-*"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_bytes(), source.read_bytes())

    def test_debug_snapshot_noop_when_disabled(self) -> None:
        mapping = MappingSet("1.0", {}, source=self.workdir / "missing.json")
        debug_snapshot(mapping)  # 未开启调试：不读取、不留档、不抛异常
        self.assertIsNone(debug_mode.debug_dir())


class SourceReaderDebugTest(unittest.TestCase):
    """源读取：调试模式下保留磁盘原始字节。"""

    def setUp(self) -> None:
        self.workdir = Path(tempfile.mkdtemp(prefix="dlp-debug-src-"))
        self.addCleanup(shutil.rmtree, self.workdir, True)

    def test_plaintext_read_archives_disk_bytes(self) -> None:
        target = self.workdir / "plain.pdf"
        target.write_bytes(b"%PDF-1.4\n%\xc7\xec\x8f\xa2\ntrailer\n%%EOF\n")
        debug_mode.set_debug_enabled(True)
        data = SourceReader().read(target)
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        saved = debug_mode.debug_dir() / "plain.pdf.disk.bin"
        self.assertTrue(saved.is_file())
        self.assertEqual(saved.read_bytes(), data)


class DebugToggleUiTest(unittest.TestCase):
    """顶栏调试模式按钮：位置、可勾选、首次提示与偏好持久化。"""

    def setUp(self) -> None:
        debug_mode.set_debug_enabled(False)
        settings_path = settings.settings_path()
        if settings_path.exists():
            settings_path.unlink()
        # 默认标记「首次提示已展示」，避免非弹窗用例被模态框阻塞
        settings.save_debug_notice_shown(True)
        self.addCleanup(
            lambda: settings_path.unlink() if settings_path.exists() else None
        )
        self.window = mw.MainWindow(AppConfig(check_updates=False), MappingSet("1.0"))
        self.addCleanup(self.window._verifier.close)
        self.addCleanup(debug_mode.set_debug_enabled, False)

    def test_button_left_of_theme_button_and_checkable(self) -> None:
        button = self.window._debug_button
        self.assertTrue(button.isCheckable())
        self.assertEqual(button.text(), "调试模式")
        bar = button.parentWidget()
        index_debug = bar.layout().indexOf(button)
        index_theme = bar.layout().indexOf(self.window._theme_button)
        index_badge = bar.layout().indexOf(self.window._mapping_badge)
        self.assertGreaterEqual(index_debug, 0)
        self.assertLess(index_debug, index_theme)
        self.assertLess(index_theme, index_badge)

    def test_toggle_applies_state_and_persists(self) -> None:
        self.window._debug_button.setChecked(True)
        self.assertTrue(debug_mode.is_debug_enabled())
        self.assertTrue(settings.load_debug_mode())
        self.assertIn("调试模式·开", self.window._debug_button.text())
        self.window._debug_button.setChecked(False)
        self.assertFalse(debug_mode.is_debug_enabled())
        self.assertFalse(settings.load_debug_mode())
        self.assertEqual(self.window._debug_button.text(), "调试模式")

    def test_first_enable_shows_notice_only_once(self) -> None:
        settings.save_debug_notice_shown(False)
        with mock.patch.object(mw.QMessageBox, "information") as information:
            self.window._debug_button.setChecked(True)
            self.assertEqual(information.call_count, 1)
            message = information.call_args[0][2]
            self.assertIn("日志文件夹", message)
            self.assertIn("中间过程文件", message)
            self.assertIn("定期清理", message)
            self.assertTrue(settings.load_debug_notice_shown())

            self.window._debug_button.setChecked(False)
            self.window._debug_button.setChecked(True)
            self.assertEqual(information.call_count, 1)  # 已提示过不再弹
            self.window._debug_button.setChecked(False)

    def test_startup_restores_saved_preference(self) -> None:
        settings.save_debug_mode(True)
        debug_mode.set_debug_enabled(True)  # 模拟 app.run() 装配顺序：先恢复再建窗
        try:
            window = mw.MainWindow(AppConfig(check_updates=False), MappingSet("1.0"))
            self.addCleanup(window._verifier.close)
            self.assertTrue(window._debug_button.isChecked())
            self.assertIn("调试模式·开", window._debug_button.text())
        finally:
            debug_mode.set_debug_enabled(False)


if __name__ == "__main__":
    unittest.main()
