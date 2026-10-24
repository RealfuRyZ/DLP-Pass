"""命令行入口单元测试（不启动图形界面、不依赖真实 DLP 环境）。"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from dlp_pass.cli import (
    DecryptSummary,
    _build_parser,
    _confirm_decrypt,
    _format_summary,
    _normalize_input_paths,
    _notify_summary,
    _shell_pipe_name,
    main,
)
from dlp_pass.core.output_mode import OutputMode


class ParserTest(unittest.TestCase):
    def test_shell_command_arguments(self) -> None:
        args = _build_parser().parse_args(
            [
                "--in-place",
                "--progress",
                "--notify",
                "--shell-batch",
                "--decrypt",
                r"C:\资料\a b.docx",
                r"D:\资料\子目录",
            ]
        )
        self.assertTrue(args.decrypt)
        self.assertTrue(args.in_place)
        self.assertTrue(args.progress)
        self.assertTrue(args.notify)
        self.assertTrue(args.shell_batch)
        self.assertFalse(args.output_dir)
        self.assertEqual(args.paths, [r"C:\资料\a b.docx", r"D:\资料\子目录"])

    def test_selftest_paths(self) -> None:
        args = _build_parser().parse_args(["--selftest", "one.pdf", "two.pdf"])
        self.assertTrue(args.selftest)
        self.assertFalse(args.decrypt)
        self.assertEqual(args.paths, ["one.pdf", "two.pdf"])

    def test_decrypt_flag_accepts_options_after_it(self) -> None:
        args = _build_parser().parse_args(["--decrypt", "--output-dir", "E:/out", "a.txt"])
        self.assertTrue(args.decrypt)
        self.assertEqual(args.output_dir, "E:/out")
        self.assertEqual(args.paths, ["a.txt"])

    def test_options_and_paths_can_be_intermixed(self) -> None:
        args = _build_parser().parse_intermixed_args(
            ["--decrypt", "a.txt", "--in-place", "b.txt"]
        )
        self.assertTrue(args.in_place)
        self.assertEqual(args.paths, ["a.txt", "b.txt"])


class NormalizePathsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dlp-cli-paths-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.file = self.tmp / "a b.txt"
        self.file.write_text("x", encoding="utf-8")

    def test_quotes_and_duplicates_are_removed(self) -> None:
        paths = _normalize_input_paths([f'"{self.file}"', str(self.file), str(self.file)])
        self.assertEqual(paths, [str(self.file)])

    def test_relative_path_becomes_absolute(self) -> None:
        old_cwd = os.getcwd()
        os.chdir(str(self.tmp))
        try:
            paths = _normalize_input_paths([self.file.name])
        finally:
            os.chdir(old_cwd)
        self.assertEqual(paths, [str(self.file)])


class SummaryTest(unittest.TestCase):
    def test_format_contains_counts_and_failure_details(self) -> None:
        summary = DecryptSummary(
            sources=3,
            tasks=2,
            success=1,
            failed=1,
            skipped_plain=1,
            missing=["D:/missing.docx"],
            failures=[("bad.pdf", "所有尝试的白名单进程均失败")],
            mode=None,
            output_root=Path("E:/out"),
            log_path=Path("C:/logs/dlp_pass.log"),
        )
        text = _format_summary(summary)
        self.assertIn("输入路径：3 个", text)
        self.assertIn("发现待解密文件：2 个", text)
        self.assertIn("成功：1 个", text)
        self.assertIn("失败：1 个", text)
        self.assertIn("跳过（未加密）：1 个", text)
        self.assertIn("D:/missing.docx", text)
        self.assertIn("bad.pdf", text)
        self.assertIn(str(Path("E:/out")), text)
        self.assertIn("dlp_pass.log", text)

    def test_ok_does_not_treat_unencrypted_skips_as_failure(self) -> None:
        self.assertTrue(DecryptSummary(sources=1, tasks=0, skipped_plain=1).ok)
        self.assertFalse(DecryptSummary(missing=["x"]).ok)
        self.assertFalse(DecryptSummary(failed=1).ok)
        self.assertFalse(DecryptSummary(skipped_conflict=1).ok)
        self.assertFalse(DecryptSummary(fatal_error="boom").ok)


class DialogTest(unittest.TestCase):
    def test_confirm_in_place_uses_warning_yes_no(self) -> None:
        with mock.patch("dlp_pass.cli._message_box", return_value=6) as box:
            self.assertTrue(_confirm_decrypt(2, OutputMode.IN_PLACE, None))
        self.assertTrue(box.call_args.kwargs["confirm"])
        self.assertIn("覆盖原文件", box.call_args.args[0])

    def test_confirm_cancel_returns_false(self) -> None:
        with mock.patch("dlp_pass.cli._message_box", return_value=7):
            self.assertFalse(_confirm_decrypt(1, OutputMode.IN_PLACE, None))

    def test_notify_titles(self) -> None:
        with mock.patch("dlp_pass.cli._message_box") as box:
            _notify_summary(DecryptSummary(sources=1, tasks=0, skipped_plain=1))
            self.assertEqual(box.call_args.args[1], "DLP-Pass 解密完成")
            self.assertFalse(box.call_args.kwargs["error"])

        with mock.patch("dlp_pass.cli._message_box") as box:
            _notify_summary(DecryptSummary(sources=1, failed=1))
            self.assertEqual(box.call_args.args[1], "DLP-Pass 解密失败")
            self.assertTrue(box.call_args.kwargs["error"])

        with mock.patch("dlp_pass.cli._message_box") as box:
            _notify_summary(DecryptSummary(sources=1, cancelled=True))
            self.assertEqual(box.call_args.args[1], "DLP-Pass 已取消")
            self.assertFalse(box.call_args.kwargs["error"])


class ShellPipeTest(unittest.TestCase):
    def test_pipe_name_is_stable_and_user_specific(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"USERNAME": "alice", "USERDOMAIN": "DOM", "SESSIONNAME": "Console"},
            clear=False,
        ):
            first = _shell_pipe_name()
            second = _shell_pipe_name()
        with mock.patch.dict(os.environ, {"USERNAME": "bob"}, clear=False):
            other = _shell_pipe_name()
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertTrue(first.startswith(r"\\.\pipe\dlp-pass-shell-"))


class MainArgumentTest(unittest.TestCase):
    def test_no_arguments_returns_usage_error(self) -> None:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = main([])
        self.assertEqual(code, 2)

    def test_decrypt_without_paths_returns_error(self) -> None:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = main(["--decrypt"])
        self.assertEqual(code, 2)

    def test_conflicting_output_modes_return_error(self) -> None:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = main(["--decrypt", "--in-place", "--output-dir", "E:/out", "x.txt"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
