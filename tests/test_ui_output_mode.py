"""界面侧「输出模式」交互测试（离屏 Qt，无需显示器）。

覆盖需求中的交互约定：
- 选择「原地解密覆盖」时，输出目录选择区域禁用（置灰），无法修改路径；
- 切回「输出到指定目录」时控件即时恢复可用；
- 「输出到指定目录」且目录无效时，点击「开始批处理」给出校验提示并阻止执行；
- 「原地解密覆盖」时任务输出路径为原始文件路径（in_place=True）。

保持 Python 3.8 兼容（Win7 构建链同样运行本测试）。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dlp_pass.config import AppConfig
from dlp_pass.core.mapping_loader import MappingSet
from dlp_pass.core.output_mode import OutputMode
from dlp_pass.ui_qt import main_window as mw
from dlp_pass.ui_qt.qt_compat import QApplication

_app = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication([])


def tearDownModule() -> None:
    # 离屏 QApplication 保留到进程结束，避免多次创建/销毁引发段错误
    pass


class _FakeSignal:
    """记录 connect 调用的信号替身。"""

    def __init__(self) -> None:
        self.slots = []

    def connect(self, slot) -> None:
        self.slots.append(slot)


class _FakeWorker:
    """替身 DecryptWorker：只记录任务，不启动线程。"""

    started = []

    def __init__(self, service, tasks, parent=None):
        self.service = service
        self.tasks = list(tasks)
        self._running = False
        self.task_started = _FakeSignal()
        self.task_finished = _FakeSignal()
        self.progress = _FakeSignal()
        self.log = _FakeSignal()
        self.finished_all = _FakeSignal()
        _FakeWorker.started.append(self)

    def isRunning(self) -> bool:
        return False

    def start(self) -> None:
        self._running = True

    def cancel(self) -> None:
        self._running = False

    def wait(self, *args) -> bool:
        return True

    def deleteLater(self) -> None:
        pass


class OutputModeUiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.window = mw.MainWindow(AppConfig(), MappingSet("1.0"))
        self.addCleanup(self.window._verifier.close)
        _FakeWorker.started = []

    def _add_entry(self, source: Path, relative: Path, suffix: str) -> str:
        item_id = str(source)
        entry = mw.FileEntry(
            item_id=item_id,
            source=source,
            relative_output=relative,
            suffix=suffix,
            size=10,
            encrypted=True,
            status="pending",
        )
        self.window._entries[item_id] = entry
        return item_id

    # ------------------------------------------------------------------ 状态

    def test_default_mode_is_directory(self) -> None:
        self.assertIs(self.window._current_output_mode(), OutputMode.DIRECTORY)
        self.assertTrue(self.window._output_edit.isEnabled())

    def test_in_place_mode_disables_output_dir_area(self) -> None:
        self.window._mode_inplace_radio.setChecked(True)
        self.assertIs(self.window._current_output_mode(), OutputMode.IN_PLACE)
        for widget in self.window._output_dir_widgets:
            self.assertFalse(widget.isEnabled())
        self.assertIn("原始文件", self.window._output_hint.text())

    def test_switching_back_re_enables_output_dir_area(self) -> None:
        self.window._mode_inplace_radio.setChecked(True)
        self.window._mode_directory_radio.setChecked(True)
        self.assertIs(self.window._current_output_mode(), OutputMode.DIRECTORY)
        for widget in self.window._output_dir_widgets:
            self.assertTrue(widget.isEnabled())

    def test_browse_ignored_in_in_place_mode(self) -> None:
        self.window._mode_inplace_radio.setChecked(True)
        with mock.patch.object(mw.QFileDialog, "getExistingDirectory") as dialog:
            self.window._on_browse_output()
        dialog.assert_not_called()

    # ------------------------------------------------------------------ 校验

    def test_start_blocked_when_output_dir_invalid(self) -> None:
        self._add_entry(Path("D:/src/a.docx"), Path("a.docx"), ".docx")
        self.window._output_edit.setText("")
        with mock.patch.object(mw.QMessageBox, "warning") as warning, \
                mock.patch.object(mw, "DecryptWorker", _FakeWorker):
            self.window._on_start()
        warning.assert_called_once()
        self.assertEqual(_FakeWorker.started, [])
        self.assertFalse(self.window._running)

    def test_start_blocked_when_output_dir_is_a_file(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="dlp-ui-"))
        self.addCleanup(_rmtree, tmp)
        as_file = tmp / "not-a-dir.txt"
        as_file.write_text("x", encoding="utf-8")
        self._add_entry(Path("D:/src/a.docx"), Path("a.docx"), ".docx")
        self.window._output_edit.setText(str(as_file))
        with mock.patch.object(mw.QMessageBox, "warning") as warning, \
                mock.patch.object(mw, "DecryptWorker", _FakeWorker):
            self.window._on_start()
        warning.assert_called_once()
        self.assertEqual(_FakeWorker.started, [])

    def test_start_in_directory_mode_when_valid(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="dlp-ui-"))
        self.addCleanup(_rmtree, tmp)
        self._add_entry(Path("D:/src/a.docx"), Path("sub/a.docx"), ".docx")
        self.window._output_edit.setText(str(tmp))
        with mock.patch.object(mw.QMessageBox, "warning") as warning, \
                mock.patch.object(mw, "DecryptWorker", _FakeWorker):
            self.window._on_start()
        warning.assert_not_called()
        self.assertEqual(len(_FakeWorker.started), 1)
        task = _FakeWorker.started[0].tasks[0]
        self.assertEqual(task.output, tmp / "sub" / "a.docx")
        self.assertFalse(task.in_place)

    def test_start_in_place_mode_targets_source_and_asks_confirmation(self) -> None:
        source = Path("D:/src/图纸/a.dwg")
        self._add_entry(source, Path("a.dwg"), ".dwg")
        self.window._mode_inplace_radio.setChecked(True)
        yes = mw.QMessageBox.StandardButton.Yes
        with mock.patch.object(mw.QMessageBox, "question", return_value=yes) as question, \
                mock.patch.object(mw, "DecryptWorker", _FakeWorker):
            self.window._on_start()
        question.assert_called_once()
        task = _FakeWorker.started[0].tasks[0]
        self.assertTrue(task.in_place)
        self.assertEqual(task.output, source)

    def test_in_place_start_cancelled_by_user(self) -> None:
        self._add_entry(Path("D:/src/a.dwg"), Path("a.dwg"), ".dwg")
        self.window._mode_inplace_radio.setChecked(True)
        no = mw.QMessageBox.StandardButton.No
        with mock.patch.object(mw.QMessageBox, "question", return_value=no), \
                mock.patch.object(mw, "DecryptWorker", _FakeWorker):
            self.window._on_start()
        self.assertEqual(_FakeWorker.started, [])
        self.assertFalse(self.window._running)


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
