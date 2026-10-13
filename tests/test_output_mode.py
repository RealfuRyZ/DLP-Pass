"""输出模式（原地解密覆盖 / 输出到指定目录）核心逻辑单元测试。

保持 Python 3.8 兼容（Win7 构建链同样运行本测试）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dlp_pass.core.output_mode import (
    OutputMode,
    plan_decrypt_tasks,
    validate_output_dir,
)


class FakeEntry:
    """测试用的条目替身（与界面 FileEntry / 核心 CollectedEntry 字段兼容）。"""

    def __init__(self, source: Path, relative_output: Path, suffix: str, item_id: str = ""):
        self.source = source
        self.relative_output = relative_output
        self.suffix = suffix
        self.item_id = item_id or str(source)


class OutputModeTest(unittest.TestCase):
    def test_labels_and_from_value(self) -> None:
        self.assertEqual(OutputMode.IN_PLACE.label, "原地解密覆盖")
        self.assertEqual(OutputMode.DIRECTORY.label, "输出到指定目录")
        self.assertIs(OutputMode.from_value("in_place"), OutputMode.IN_PLACE)
        self.assertIs(OutputMode.from_value("IN_PLACE"), OutputMode.IN_PLACE)
        # 无法识别时回退到安全默认值（输出到指定目录）
        self.assertIs(OutputMode.from_value("nope"), OutputMode.DIRECTORY)
        self.assertIs(OutputMode.from_value(None), OutputMode.DIRECTORY)


class ValidateOutputDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dlp-outdir-"))
        self.addCleanup(_rmtree, self.tmp)

    def test_empty_is_rejected(self) -> None:
        valid, message, resolved = validate_output_dir("   ")
        self.assertFalse(valid)
        self.assertIn("请先选择", message)
        self.assertIsNone(resolved)

    def test_existing_directory_ok(self) -> None:
        valid, message, resolved = validate_output_dir(self.tmp)
        self.assertTrue(valid)
        self.assertEqual(message, "")
        assert resolved is not None
        self.assertTrue(resolved.is_dir())

    def test_existing_file_is_rejected(self) -> None:
        file_path = self.tmp / "note.txt"
        file_path.write_text("x", encoding="utf-8")
        valid, message, _ = validate_output_dir(file_path)
        self.assertFalse(valid)
        self.assertIn("不是文件夹", message)

    def test_relative_path_is_rejected(self) -> None:
        valid, message, _ = validate_output_dir("解密输出/子目录")
        self.assertFalse(valid)
        self.assertIn("绝对路径", message)

    def test_invalid_chars_are_rejected(self) -> None:
        valid, message, _ = validate_output_dir(r"C:\输出<bad>|dir")
        self.assertFalse(valid)
        self.assertIn("非法字符", message)

    def test_missing_dir_allowed_by_default_rejected_when_required(self) -> None:
        missing = self.tmp / "not-created-yet"
        valid, _, resolved = validate_output_dir(missing)
        self.assertTrue(valid)
        self.assertEqual(resolved, missing)
        valid2, message2, _ = validate_output_dir(missing, must_exist=True)
        self.assertFalse(valid2)
        self.assertIn("不存在", message2)


class PlanTasksTest(unittest.TestCase):
    def test_directory_mode_uses_relative_output(self) -> None:
        entries = [
            FakeEntry(Path("D:/src/a.docx"), Path("proj/a.docx"), ".docx", "i1"),
            FakeEntry(Path("D:/src/b/b.pdf"), Path("proj/b/b.pdf"), ".pdf", "i2"),
        ]
        tasks, conflicts = plan_decrypt_tasks(entries, OutputMode.DIRECTORY, Path("E:/out"))
        self.assertEqual(conflicts, [])
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].output, Path("E:/out/proj/a.docx"))
        self.assertEqual(tasks[1].output, Path("E:/out/proj/b/b.pdf"))
        self.assertFalse(tasks[0].in_place)

    def test_directory_mode_reports_conflicts(self) -> None:
        entries = [
            FakeEntry(Path("D:/src/a.docx"), Path("a.docx"), ".docx", "i1"),
            FakeEntry(Path(r"D:\src\sub\a.docx"), Path("a.docx"), ".docx", "i2"),
        ]
        tasks, conflicts = plan_decrypt_tasks(entries, OutputMode.DIRECTORY, Path("E:/out"))
        self.assertEqual(len(tasks), 1)
        self.assertEqual(conflicts, [("i2", "输出路径与其它文件冲突，已跳过")])

    def test_in_place_mode_targets_source_path(self) -> None:
        source = Path("D:/src/图纸/a.dwg")
        entries = [FakeEntry(source, Path("a.dwg"), ".dwg", "i1")]
        tasks, conflicts = plan_decrypt_tasks(entries, OutputMode.IN_PLACE)
        self.assertEqual(conflicts, [])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].source, source)
        self.assertEqual(tasks[0].output, source)
        self.assertTrue(tasks[0].in_place)
        self.assertEqual(tasks[0].suffix, ".dwg")

    def test_in_place_mode_requires_no_output_root(self) -> None:
        entries = [FakeEntry(Path("D:/src/a.txt"), Path("a.txt"), ".txt", "i1")]
        tasks, _ = plan_decrypt_tasks(entries, OutputMode.IN_PLACE)
        self.assertEqual(tasks[0].output, Path("D:/src/a.txt"))

    def test_directory_mode_without_root_raises(self) -> None:
        entries = [FakeEntry(Path("D:/src/a.txt"), Path("a.txt"), ".txt", "i1")]
        with self.assertRaises(ValueError):
            plan_decrypt_tasks(entries, OutputMode.DIRECTORY)


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
