"""发布映射版本 20261026.1 的回归测试（针对仓库内真实文件，不依赖临时构造数据）。

校验点：
- 新版本是 ``mappings/`` 目录中当前生效版本（版本号最大）；
- 完整保留上一版本 20261016.1 的全部规则（不改写既有后缀的进程队列）；
- 结构合法：extension 小写含点、唯一、primary 非空且不含 ``.exe`` / 空格；
- 需求覆盖：工程类（CAD/BIM/CAE/EDA/PLM）与设计类常见软件均有映射；
- 本版本追加的 53 条文档 / 数据库 / 编程语言 / 配置脚本 / 3D 交换格式 /
  音视频 / 字体后缀全部存在且主进程与需求一致。

保持 Python 3.8 兼容（Win7 构建链同样运行本测试）。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from dlp_pass.core.mapping_loader import load_latest, load_mapping_file, version_key

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAPPING_DIR = PROJECT_ROOT / "mappings"
NEW_VERSION = "20261026.1"
BASE_VERSION = "20261016.1"

# 20261026.1 相对 20261016.1 追加的全部后缀（53 条）
ADDED_EXTENSIONS = [
    ".md", ".markdown", ".rst", ".tex",
    ".db", ".sqlite", ".sqlite3", ".sql", ".mdb", ".mdf", ".ldf",
    ".go", ".rs", ".rb", ".php", ".c", ".h", ".css", ".html", ".ts", ".swift", ".kt",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".bat", ".cmd", ".ps1", ".sh",
    ".stl", ".obj", ".fbx", ".gltf", ".glb", ".step", ".stp", ".iges", ".igs",
    ".mp4", ".avi", ".mkv", ".mov", ".mp3", ".flac", ".wav",
    ".ttf", ".otf", ".ttc", ".woff", ".woff2",
]

# 追加条目中抽样的「后缀 -> 首选进程」约定
ADDED_SPOT_QUEUES = {
    ".md": "code",
    ".tex": "texstudio",
    ".db": "DBBrowserForSQLite",
    ".sql": "DBeaver",
    ".mdf": "SSMS",
    ".go": "GoLand",
    ".rs": "CLion",
    ".php": "PhpStorm",
    ".c": "devenv",
    ".ts": "WebStorm",
    ".kt": "idea",
    ".ini": "notepad",
    ".bat": "cmd",
    ".ps1": "powershell",
    ".sh": "bash",
    ".stl": "blender",
    ".fbx": "maya",
    ".step": "CNEXT",
    ".mp4": "PotPlayer",
    ".mp3": "foobar2000",
    ".wav": "audacity",
    ".ttf": "WindowsFontViewer",
    ".woff2": "chrome",
}

# 需求点名要覆盖的软件 -> 其典型文档后缀（至少命中一个即可）
REQUIRED_COVERAGE = {
    "AutoCAD": (".dwg", ".dwt", ".dws"),
    "SolidWorks": (".sldprt", ".sldasm", ".slddrt"),
    "CATIA": (".catpart", ".catproduct", ".catdrawing"),
    "Creo/Pro-E": (".prt", ".asm", ".drw", ".neu"),
    "NX/UG": (".prt", ".clr", ".dax", ".ttl"),
    "Revit": (".rvt", ".rfa", ".rft"),
    "ArchiCAD": (".pln", ".apr"),
    "Navisworks": (".nwd", ".nwc"),
    "ANSYS": (".wbpj", ".aedt"),
    "ABAQUS": (".cae", ".inp"),
    "COMSOL": (".mph",),
    "HyperMesh": (".hm", ".fem", ".mesh"),
    "LS-DYNA": (".k", ".dyn", ".key"),
    "Altium Designer": (".pcbdoc", ".schdoc", ".pcblib", ".outjob"),
    "Cadence Virtuoso": (".scs", ".ssn", ".cdl"),
    "Siemens EDA/PADS": (".pcb", ".brd", ".rts"),
    "华大九天 Empyrean": (".net", ".sp", ".spice"),
    "Teamcenter": (".tc", ".tcc", ".xdm"),
    "Windchill": (".wt",),
    "Aras Innovator": (".aras", ".isc"),
    "Photoshop": (".psd", ".psb", ".pdd"),
    "Illustrator": (".ai", ".eps", ".svg"),
    "InDesign": (".indd", ".indt", ".inx"),
    "Lightroom": (".lrcat",),
    "Figma": (".fig",),
    "Sketch": (".sketch",),
    "CorelDRAW": (".cdr", ".cmx", ".cdt"),
    "Affinity Designer": (".afdesign",),
    "Affinity Photo": (".afphoto",),
    "Affinity Publisher": (".afpub",),
    "Blender": (".blend", ".blend1"),
    "Substance 3D": (".sbs", ".sbsar"),
    "3ds Max": (".max", ".ms", ".3ds"),
    "Maya": (".ma", ".mb"),
    "ZBrush": (".ztl", ".zpr"),
    "Premiere Pro": (".prproj",),
    "After Effects": (".aep", ".aet"),
    "DaVinci Resolve": (".drp", ".resolve"),
    "国产设计客户端": (".ckd", ".gdd", ".jsdesign", ".pixso", ".mg"),
}


def _rules_by_extension(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return {rule["extension"]: rule for rule in data["rules"]}


class NewMappingFileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.path = MAPPING_DIR / (NEW_VERSION + ".json")
        cls.mapping = load_mapping_file(cls.path)
        cls.rules = _rules_by_extension(cls.path)

    def test_file_exists_and_parses(self) -> None:
        self.assertTrue(self.path.is_file(), "缺少新版本映射文件")
        self.assertIsNotNone(self.mapping)
        self.assertEqual(self.mapping.version, NEW_VERSION)

    def test_is_active_version(self) -> None:
        active = load_latest(config=None, project_root=PROJECT_ROOT)
        self.assertEqual(active.version, NEW_VERSION)
        self.assertGreater(version_key(BASE_VERSION), version_key("0"))
        self.assertLess(version_key(BASE_VERSION), version_key(NEW_VERSION))

    def test_previous_version_fully_preserved(self) -> None:
        base = _rules_by_extension(MAPPING_DIR / (BASE_VERSION + ".json"))
        for extension, rule in base.items():
            self.assertIn(extension, self.rules, "新版本丢失了旧后缀 %s" % extension)
            self.assertEqual(self.rules[extension]["primary"], rule["primary"])
            self.assertEqual(list(self.rules[extension]["alternatives"]), list(rule["alternatives"]))

    def test_expanded_coverage(self) -> None:
        base = _rules_by_extension(MAPPING_DIR / (BASE_VERSION + ".json"))
        self.assertGreater(len(self.rules), len(base) + 50)
        for software, extensions in REQUIRED_COVERAGE.items():
            hit = [ext for ext in extensions if ext in self.rules]
            self.assertTrue(hit, "%s 未覆盖任何后缀（候选：%s）" % (software, extensions))
            for ext in hit:
                self.assertTrue(self.rules[ext]["primary"], "%s 的主进程名为空" % ext)

    def test_added_extensions_present(self) -> None:
        """本版本追加的 53 条后缀全部存在（且不与旧版本重叠）。"""
        base = _rules_by_extension(MAPPING_DIR / (BASE_VERSION + ".json"))
        self.assertEqual(len(ADDED_EXTENSIONS), 53)
        for extension in ADDED_EXTENSIONS:
            self.assertIn(extension, self.rules, "缺少追加后缀 %s" % extension)
            self.assertNotIn(extension, base, "%s 应为本版本新增" % extension)

    def test_added_spot_process_queues(self) -> None:
        for extension, primary in ADDED_SPOT_QUEUES.items():
            self.assertEqual(self.mapping.get_process_queue(extension)[0], primary)
        # 抽样校验完整队列顺序（主进程 + 备用顺序与需求一致）
        self.assertEqual(
            self.mapping.get_process_queue(".md"),
            ["code", "Typora", "Obsidian", "notepad++"],
        )
        self.assertEqual(
            self.mapping.get_process_queue(".step"),
            ["CNEXT", "SLDWORKS", "Inventor", "Fusion360"],
        )
        self.assertEqual(
            self.mapping.get_process_queue(".mp3"),
            ["foobar2000", "vlc", "GrooveMusic"],
        )
        self.assertEqual(
            self.mapping.get_process_queue(".ttf"), ["WindowsFontViewer"],
        )

    def test_structure_rules(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        self.assertEqual(data["version"], NEW_VERSION)
        seen = set()
        for rule in data["rules"]:
            extension = rule["extension"]
            self.assertTrue(extension.startswith("."), extension)
            self.assertEqual(extension, extension.lower(), extension)
            self.assertNotIn(extension, seen, "重复后缀 %s" % extension)
            seen.add(extension)
            names = [rule["primary"]] + list(rule.get("alternatives", []))
            for name in names:
                self.assertTrue(name, "%s 存在空进程名" % extension)
                self.assertNotIn(".exe", name.lower(), name)
                self.assertNotIn(" ", name, "进程名不应含空格：%s" % name)
            self.assertEqual(rule.get("priority"), 100)
            self.assertTrue(rule.get("enabled"))

    def test_spot_process_queues(self) -> None:
        expected = {
            ".rvt": "Revit",
            ".catpart": "CNEXT",
            ".afdesign": "AffinityDesigner",
            ".sbsar": "substancedesigner",
            ".wbpj": "AnsysWBU",
            ".mph": "comsol",
            ".tc": "tc",
            ".docx": "winword",
            ".pdf": "pdfeditor",
        }
        for extension, primary in expected.items():
            self.assertEqual(self.mapping.get_process_queue(extension)[0], primary)

    def test_no_bom_and_utf8(self) -> None:
        raw = self.path.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "映射文件不应带 BOM")
        raw.decode("utf-8")


if __name__ == "__main__":
    unittest.main()
