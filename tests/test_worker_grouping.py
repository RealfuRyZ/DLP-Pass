"""后台任务分组排序（按后缀分组）单元测试。

对应批量解密优化：DecryptWorker 在后台把任务按后缀分组排序，同后缀任务连续
处理，使核心服务可以复用同一个常驻伪装进程；前台列表顺序不受影响。

保持 Python 3.8 兼容（Win7 构建链同样运行本测试）。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from dlp_pass.core.models import DecryptTask
from dlp_pass.ui_qt.workers import group_tasks_by_suffix


def _task(suffix: str, name: str) -> DecryptTask:
    source = Path("D:/src") / name
    return DecryptTask(source=source, output=Path("E:/out") / name, suffix=suffix)


class GroupTasksBySuffixTest(unittest.TestCase):
    def test_groups_same_suffix_contiguously_preserving_order(self):
        tasks = [
            _task(".docx", "a.docx"),
            _task(".pdf", "b.pdf"),
            _task(".docx", "c.docx"),
            _task(".pdf", "d.pdf"),
        ]
        names = [task.source.name for task in group_tasks_by_suffix(tasks)]
        self.assertEqual(names, ["a.docx", "c.docx", "b.pdf", "d.pdf"])

    def test_empty_and_single(self):
        self.assertEqual(group_tasks_by_suffix([]), [])
        only = [_task(".dwg", "图.dwg")]
        self.assertEqual(group_tasks_by_suffix(only), only)

    def test_suffix_case_is_normalized_for_grouping(self):
        tasks = [_task(".DOCX", "a.docx"), _task(".pdf", "b.pdf"), _task(".docx", "c.docx")]
        names = [task.source.name for task in group_tasks_by_suffix(tasks)]
        self.assertEqual(names, ["a.docx", "c.docx", "b.pdf"])

    def test_does_not_mutate_input_order(self):
        tasks = [_task(".pdf", "b.pdf"), _task(".docx", "a.docx")]
        group_tasks_by_suffix(tasks)
        self.assertEqual([task.source.name for task in tasks], ["b.pdf", "a.docx"])


if __name__ == "__main__":
    unittest.main()
