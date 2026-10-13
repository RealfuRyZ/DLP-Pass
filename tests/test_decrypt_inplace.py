"""「原地解密覆盖」的安全性单元测试（核心层，不依赖界面与真实 DLP 环境）。

用替身组件（映射 / 伪装进程 / 密文检测 / 源读取）驱动 :class:`DecryptService`，
重点验证：导出仍为密文时绝不改动原始文件，导出为明文时才覆盖原文件。

保持 Python 3.8 兼容（Win7 构建链同样运行本测试）。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dlp_pass.core.decrypt_service import DecryptService
from dlp_pass.core.models import DecryptTask

MARKER = "%TSD-Header-###%"
PLAINTEXT = b"HELLO PLAIN TEXT"
CIPHERTEXT = MARKER.encode("utf-8") + b"ENCRYPTED BLOB"

class FakeMapping:
    """固定进程队列的映射替身。"""

    def __init__(self, queue):
        self._queue = list(queue)

    def get_process_queue(self, suffix):
        return list(self._queue)


class FakeVerifier:
    """按文件头是否含密文标识判定加密状态（无需 PowerShell）。"""

    def is_encrypted(self, path):
        target = Path(path)
        if not target.exists():
            return True
        return MARKER in target.read_bytes()[:64].decode("latin-1")

    def close(self):
        pass


class FakeWriters:
    """伪装进程构建替身。"""

    def get_writer(self, proc_name):
        return Path("%s.exe" % proc_name)


class FakeSubprocess:
    """替身 subprocess 模块：伪装卸包写盘，可指定每个进程名写出的内容。"""

    OUTPUTS = {}  # 进程名 -> 落盘字节（测试中设置）
    PIPE = -1     # 仅为替身：真实 subprocess.PIPE 语义在本测试中无副作用
    DEVNULL = -2

    class Popen:
        def __init__(self, cmd, **kwargs):
            self.cmd = list(cmd)

        def communicate(self, input=None, timeout=None):
            proc_name = Path(self.cmd[0]).stem
            payload = FakeSubprocess.OUTPUTS.get(proc_name, CIPHERTEXT)
            with open(self.cmd[1], "wb") as fh:
                fh.write(payload)
            return (b"", b"")

    @staticmethod
    def run(cmd, **kwargs):
        # 仅重命名兜底分支使用；测试里直接执行 replace
        text = str(cmd)
        if "move /y" in text:
            parts = text.split('"')
            if len(parts) >= 4:
                os.replace(parts[1], parts[3])
        return None


class DecryptInPlaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dlp-inplace-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        FakeSubprocess.OUTPUTS = {}
        self._patcher = mock.patch(
            "dlp_pass.core.decrypt_service.subprocess", FakeSubprocess
        )
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _service(self, queue=("notepad", "winword")) -> DecryptService:
        return DecryptService(
            mapping=FakeMapping(queue),
            verifier=FakeVerifier(),
            writer_factory=FakeWriters(),
            source_reader=_FakeReader(),
        )

    def _make_source(self, name: str = "报告.docx", on_disk: bytes = CIPHERTEXT) -> Path:
        path = self.tmp / name
        path.write_bytes(on_disk)
        return path

    # ------------------------------------------------------------------ 用例

    def test_success_overwrites_original(self) -> None:
        source = self._make_source()
        FakeSubprocess.OUTPUTS["notepad"] = PLAINTEXT
        task = DecryptTask(source=source, output=source, suffix=".docx", in_place=True)
        outcome = self._service().decrypt_file(task)

        self.assertTrue(outcome.success, outcome.error)
        self.assertEqual(outcome.process_name, "notepad")
        self.assertEqual(source.read_bytes(), PLAINTEXT)
        self.assertFalse(list(self.tmp.glob("*.pass")))
        self.assertFalse(list(self.tmp.glob("*.dlpbak")))

    def test_ciphertext_leaves_original_untouched(self) -> None:
        source = self._make_source()
        task = DecryptTask(source=source, output=source, suffix=".docx", in_place=True)
        outcome = self._service(queue=("notepad",)).decrypt_file(task)

        self.assertFalse(outcome.success)
        self.assertEqual(source.read_bytes(), CIPHERTEXT)      # 原文件未被破坏
        self.assertEqual(outcome.attempts, ["notepad: 导出仍为密文（原文件未改动）"])
        self.assertFalse(list(self.tmp.glob("*.pass")))         # 临时文件已清理

    def test_falls_back_to_next_process_without_touching_original(self) -> None:
        source = self._make_source(name="图纸.dwg")
        FakeSubprocess.OUTPUTS["notepad"] = CIPHERTEXT
        FakeSubprocess.OUTPUTS["winword"] = PLAINTEXT
        task = DecryptTask(source=source, output=source, suffix=".dwg", in_place=True)
        outcome = self._service().decrypt_file(task)

        self.assertTrue(outcome.success, outcome.error)
        self.assertEqual(outcome.process_name, "winword")
        self.assertEqual(source.read_bytes(), PLAINTEXT)

    def test_cancel_before_attempt_keeps_original(self) -> None:
        source = self._make_source()
        task = DecryptTask(source=source, output=source, suffix=".docx", in_place=True)
        outcome = self._service().decrypt_file(task, is_cancelled=lambda: True)

        self.assertTrue(outcome.cancelled)
        self.assertFalse(outcome.success)
        self.assertEqual(source.read_bytes(), CIPHERTEXT)

    def test_directory_mode_still_writes_to_output_only(self) -> None:
        source = self._make_source(name="目录.docx")
        output = self.tmp / "out" / "目录.docx"
        FakeSubprocess.OUTPUTS["notepad"] = PLAINTEXT
        task = DecryptTask(source=source, output=output, suffix=".docx")
        outcome = self._service().decrypt_file(task)

        self.assertTrue(outcome.success, outcome.error)
        self.assertEqual(output.read_bytes(), PLAINTEXT)
        self.assertEqual(source.read_bytes(), CIPHERTEXT)      # 原文件保持不变

    def test_directory_mode_removes_ciphertext_output(self) -> None:
        source = self._make_source(name="目录2.docx")
        output = self.tmp / "out2" / "目录2.docx"
        task = DecryptTask(source=source, output=output, suffix=".docx")
        outcome = self._service(queue=("notepad",)).decrypt_file(task)

        self.assertFalse(outcome.success)
        self.assertFalse(output.exists())
        self.assertEqual(source.read_bytes(), CIPHERTEXT)


class _FakeReader:
    """源读取替身：始终返回明文（等价于 DLP 驱动为受信进程自动解密后的读取结果）。"""

    def read(self, path):
        return PLAINTEXT


if __name__ == "__main__":
    unittest.main()
