"""mappings/ 版本化映射加载器的单元测试。

约定测试重点：启动时始终取版本号最大的有效文件；草稿/坏文件被跳过；
缺失时回退内置兜底规则。代码保持 Python 3.8 兼容（Win7 构建链同样运行本测试）。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dlp_pass.config import AppConfig, ProcessRule
from dlp_pass.core.mapping_loader import (
    MappingSet,
    find_mapping_dir,
    load_latest,
    load_mapping_file,
    version_key,
)


def write_version(directory: Path, name: str, rules: list, **extra) -> Path:
    """在测试目录内写一个版本文件：``<name>.json``（extra 覆盖 JSON 字段）。"""
    data = {"version": name, "rules": rules}
    data.update(extra)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


class VersionKeyTest(unittest.TestCase):
    """版本号排序键：数字段按数值、混合段安全可比。"""

    def test_numeric_ordering(self):
        self.assertLess(version_key("20261009.1"), version_key("20261012.1"))
        self.assertLess(version_key("20261009.1"), version_key("20261009.2"))
        self.assertLess(version_key("20260101.9"), version_key("20260101.10"))

    def test_mixed_segments(self):
        self.assertLess(version_key("v1.2"), version_key("v1.10"))
        self.assertLess(version_key("20261009"), version_key("20261009.1"))
        self.assertEqual(version_key("2026_10_09.1"), version_key("2026.10-09.1"))


class LoaderTest(unittest.TestCase):
    """load_latest 行为。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dlp-mapping-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_latest_version_wins(self) -> None:
        write_version(self.tmp, "20261001.1", [{"extension": ".txt", "primary": "old"}])
        write_version(self.tmp, "20261009.1", [{"extension": ".txt", "primary": "new"}])
        mapping = load_latest(config=AppConfig(mappings_dir=self.tmp))
        self.assertEqual(mapping.version, "20261009.1")
        self.assertEqual(mapping.get_process_queue(".txt"), ["new"])

    def test_priority_and_enabled(self) -> None:
        write_version(
            self.tmp,
            "1.0",
            [
                {"extension": ".docx", "primary": "low", "priority": 1},
                {"extension": ".docx", "primary": "high", "priority": 5,
                 "alternatives": ["alt1", "alt2"]},
                {"extension": ".xls", "primary": "disabled", "enabled": False},
            ],
        )
        mapping = load_latest(config=AppConfig(mappings_dir=self.tmp))
        self.assertEqual(mapping.get_process_queue(".docx"), ["high", "alt1", "alt2"])
        # enabled=false 被忽略 -> 走内置兜底
        self.assertEqual(mapping.get_process_queue(".xls"), ["notepad", "winword", "wps"])

    def test_comma_alternatives(self) -> None:
        write_version(self.tmp, "1.0",
                      [{"extension": ".a", "primary": "x", "alternatives": "y, z"}])
        mapping = load_latest(config=AppConfig(mappings_dir=self.tmp))
        self.assertEqual(mapping.get_process_queue(".a"), ["x", "y", "z"])

    def test_malformed_files_skipped(self) -> None:
        (self.tmp / "9.0.json").write_text("{not json", encoding="utf-8")
        (self.tmp / "99.0.json").write_text(json.dumps({"rules": "not-a-list"}), encoding="utf-8")
        write_version(self.tmp, "8.0", [{"extension": ".a", "primary": "x"}])
        mapping = load_latest(config=AppConfig(mappings_dir=self.tmp))
        self.assertEqual(mapping.version, "8.0")

    def test_draft_files_ignored(self) -> None:
        write_version(self.tmp, "1.0", [{"extension": ".a", "primary": "x"}])
        write_version(self.tmp, "_draft", [{"extension": ".a", "primary": "zzz"}])
        mapping = load_latest(config=AppConfig(mappings_dir=self.tmp))
        self.assertEqual(mapping.version, "1.0")

    def test_version_mismatch_uses_filename(self) -> None:
        write_version(self.tmp, "3.0", [{"extension": ".a", "primary": "x"}], version="2.9")
        mapping = load_latest(config=AppConfig(mappings_dir=self.tmp))
        self.assertEqual(mapping.version, "3.0")

    def test_empty_and_missing_dir(self) -> None:
        mapping = load_latest(config=AppConfig(mappings_dir=self.tmp))
        self.assertTrue(mapping.is_fallback)
        missing = self.tmp / "nope"
        mapping2 = load_latest(config=AppConfig(mappings_dir=missing))
        self.assertTrue(mapping2.is_fallback)

    def test_bom_file_supported(self) -> None:
        path = write_version(self.tmp, "1.0", [{"extension": ".a", "primary": "x"}])
        bom_text = "\ufeff" + path.read_text(encoding="utf-8")   # 记事本/PowerShell 常见 BOM
        path.write_text(bom_text, encoding="utf-8")
        mapping = load_latest(config=AppConfig(mappings_dir=self.tmp))
        self.assertEqual(mapping.version, "1.0")

    def test_project_root_lookup(self) -> None:
        (self.tmp / "mappings").mkdir()
        write_version(self.tmp / "mappings", "1.0", [{"extension": ".a", "primary": "x"}])
        mapping = load_latest(config=None, project_root=self.tmp)
        self.assertEqual(mapping.version, "1.0")
        self.assertEqual(find_mapping_dir(config=None, project_root=self.tmp),
                         self.tmp / "mappings")

    def test_single_file_parse(self) -> None:
        path = write_version(self.tmp, "1.0",
                             [{"extension": ".A", "primary": " P "}, {"extension": "", "primary": "x"}])
        mapping = load_mapping_file(path)
        self.assertIsNotNone(mapping)
        assert mapping is not None
        self.assertEqual(mapping.get_process_queue(".a"), ["P"])   # 后缀小写、进程名去空白
        self.assertNotIn("", mapping.rules)                        # 缺 extension 的条目被丢弃


class FallbackTest(unittest.TestCase):
    """内置兜底规则与描述文本。"""

    def test_pdf_fallback(self) -> None:
        mapping = MappingSet("内置兜底", is_fallback=True)
        self.assertEqual(mapping.get_process_queue(".pdf"),
                         ["pdfeditor", "AcroRd32", "FoxitPDFEditor"])
        self.assertEqual(mapping.get_process_queue(".anything"), ["notepad", "winword", "wps"])

    def test_describe(self) -> None:
        self.assertIn("兜底", MappingSet("内置兜底", is_fallback=True).describe())
        real = MappingSet("1.0", {".a": ProcessRule("x")})
        self.assertIn("1.0", real.describe())


if __name__ == "__main__":
    unittest.main()
