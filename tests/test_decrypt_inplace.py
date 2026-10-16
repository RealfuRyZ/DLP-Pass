"""核心层解密服务单元测试（不依赖界面与真实 DLP 环境）。

用替身组件（映射 / 伪装进程 / 密文检测 / 源读取）驱动 :class:`DecryptService`，
覆盖两类重点：

1. 「原地解密覆盖」安全性：导出仍为密文时绝不改动原始文件，导出为明文时才覆盖；
2. 批处理进程复用：同后缀连续任务只启动一次常驻伪装进程，后缀切换与
   :meth:`DecryptService.close` 都会关闭上一分组的进程（对应 DecryptWorker
   的后缀分组排序优化）。

保持 Python 3.8 兼容（Win7 构建链同样运行本测试）。
"""

from __future__ import annotations

import base64
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


class _FakeStdin:
    """替身进程 stdin：按批处理协议在 flush 时解析请求并落盘。"""

    def __init__(self, proc):
        self._proc = proc
        self._buffer = bytearray()
        self.closed = False

    def write(self, data):
        self._buffer.extend(data)
        return len(data)

    def flush(self):
        self._drain()

    def close(self):
        self.closed = True

    def _drain(self):
        while True:
            index = self._buffer.find(b"\n")
            if index < 0:
                return
            header = bytes(self._buffer[:index])
            del self._buffer[: index + 1]
            try:
                token, size_text = header.split()
                size = int(size_text)
            except ValueError:
                self._proc.stdout.push(b"ERR bad-header\n")
                return
            if len(self._buffer) < size:  # 数据尚未写完：头部放回等待下次 flush
                self._buffer[:0] = header + b"\n"
                return
            del self._buffer[:size]
            target = Path(os.fsdecode(base64.b64decode(token)))
            proc_name = Path(self._proc.cmd[0]).stem
            payload = FakeSubprocess.OUTPUTS.get(proc_name, CIPHERTEXT)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            self._proc.stdout.push(b"OK\n")


class _FakeStdout:
    """替身进程 stdout：按请求顺序返回 ``OK`` 应答。"""

    def __init__(self):
        self._pending = bytearray()
        self.closed = False

    def push(self, data):
        self._pending.extend(data)

    def readline(self):
        index = self._pending.find(b"\n")
        if index < 0:
            if not self._pending:
                return b""
            line = bytes(self._pending)
            self._pending.clear()
            return line
        line = bytes(self._pending[: index + 1])
        del self._pending[: index + 1]
        return line

    def close(self):
        self.closed = True


class FakeSubprocess:
    """替身 subprocess 模块：模拟常驻伪装进程的批处理 stdin/stdout 协议。

    ``OUTPUTS`` 指定各进程名落盘的字节内容（默认密文）；``created`` 记录每次
    启动的进程名，``processes`` 记录进程对象，用于断言进程复用与关闭时机。
    """

    OUTPUTS = {}
    created = []
    processes = []
    PIPE = -1
    DEVNULL = -2

    class Popen:
        def __init__(self, cmd, **kwargs):
            self.cmd = list(cmd)
            self.returncode = None
            self.stdin = _FakeStdin(self)
            self.stdout = _FakeStdout()
            FakeSubprocess.created.append(Path(self.cmd[0]).stem)
            FakeSubprocess.processes.append(self)

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            if self.returncode is None:
                self.returncode = 0
            return self.returncode

        def kill(self):
            self.returncode = -9

    @staticmethod
    def run(cmd, **kwargs):
        # 仅重命名兜底分支使用；测试里直接执行 replace
        text = str(cmd)
        if "move /y" in text:
            parts = text.split('"')
            if len(parts) >= 4:
                os.replace(parts[1], parts[3])
        return None


class _FakeReader:
    """源读取替身：始终返回明文（等价于 DLP 驱动为受信进程自动解密后的读取结果）。"""

    def read(self, path):
        return PLAINTEXT


class _ServiceTestBase(unittest.TestCase):
    """公共夹具：临时目录 + 替身 subprocess 补丁 + 服务构造。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlp-service-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        FakeSubprocess.OUTPUTS = {}
        FakeSubprocess.created = []
        FakeSubprocess.processes = []
        self._patcher = mock.patch(
            "dlp_pass.core.decrypt_service.subprocess", FakeSubprocess
        )
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _service(self, queue=("notepad", "winword")):
        return DecryptService(
            mapping=FakeMapping(queue),
            verifier=FakeVerifier(),
            writer_factory=FakeWriters(),
            source_reader=_FakeReader(),
        )

    def _make_source(self, name="报告.docx", on_disk=CIPHERTEXT):
        path = self.tmp / name
        path.write_bytes(on_disk)
        return path


class DecryptInPlaceTest(_ServiceTestBase):
    """「原地解密覆盖」与目录输出模式的既有用例。"""

    def test_success_overwrites_original(self):
        source = self._make_source()
        FakeSubprocess.OUTPUTS["notepad"] = PLAINTEXT
        task = DecryptTask(source=source, output=source, suffix=".docx", in_place=True)
        outcome = self._service().decrypt_file(task)

        self.assertTrue(outcome.success, outcome.error)
        self.assertEqual(outcome.process_name, "notepad")
        self.assertEqual(source.read_bytes(), PLAINTEXT)
        self.assertFalse(list(self.tmp.glob("*.pass")))
        self.assertFalse(list(self.tmp.glob("*.dlpbak")))

    def test_ciphertext_leaves_original_untouched(self):
        source = self._make_source()
        task = DecryptTask(source=source, output=source, suffix=".docx", in_place=True)
        outcome = self._service(queue=("notepad",)).decrypt_file(task)

        self.assertFalse(outcome.success)
        self.assertEqual(source.read_bytes(), CIPHERTEXT)      # 原文件未被破坏
        self.assertEqual(outcome.attempts, ["notepad: 导出仍为密文（原文件未改动）"])
        self.assertFalse(list(self.tmp.glob("*.pass")))         # 临时文件已清理

    def test_falls_back_to_next_process_without_touching_original(self):
        source = self._make_source(name="图纸.dwg")
        FakeSubprocess.OUTPUTS["notepad"] = CIPHERTEXT
        FakeSubprocess.OUTPUTS["winword"] = PLAINTEXT
        task = DecryptTask(source=source, output=source, suffix=".dwg", in_place=True)
        outcome = self._service().decrypt_file(task)

        self.assertTrue(outcome.success, outcome.error)
        self.assertEqual(outcome.process_name, "winword")
        self.assertEqual(source.read_bytes(), PLAINTEXT)

    def test_cancel_before_attempt_keeps_original(self):
        source = self._make_source()
        task = DecryptTask(source=source, output=source, suffix=".docx", in_place=True)
        outcome = self._service().decrypt_file(task, is_cancelled=lambda: True)

        self.assertTrue(outcome.cancelled)
        self.assertFalse(outcome.success)
        self.assertEqual(source.read_bytes(), CIPHERTEXT)

    def test_directory_mode_still_writes_to_output_only(self):
        source = self._make_source(name="目录.docx")
        output = self.tmp / "out" / "目录.docx"
        FakeSubprocess.OUTPUTS["notepad"] = PLAINTEXT
        task = DecryptTask(source=source, output=output, suffix=".docx")
        outcome = self._service().decrypt_file(task)

        self.assertTrue(outcome.success, outcome.error)
        self.assertEqual(output.read_bytes(), PLAINTEXT)
        self.assertEqual(source.read_bytes(), CIPHERTEXT)      # 原文件保持不变

    def test_directory_mode_removes_ciphertext_output(self):
        source = self._make_source(name="目录2.docx")
        output = self.tmp / "out2" / "目录2.docx"
        task = DecryptTask(source=source, output=output, suffix=".docx")
        outcome = self._service(queue=("notepad",)).decrypt_file(task)

        self.assertFalse(outcome.success)
        self.assertFalse(output.exists())
        self.assertEqual(source.read_bytes(), CIPHERTEXT)


class BatchProcessReuseTest(_ServiceTestBase):
    """批处理后缀分组复用常驻伪装进程的用例。"""

    def _task(self, source: Path, suffix: str) -> DecryptTask:
        return DecryptTask(source=source, output=self.tmp / "out" / source.name, suffix=suffix)

    def test_same_suffix_batch_starts_writer_process_once(self):
        FakeSubprocess.OUTPUTS["notepad"] = PLAINTEXT
        service = self._service(queue=("notepad",))
        tasks = [self._task(self._make_source(name="批量%d.docx" % i), ".docx") for i in range(3)]

        outcomes = [service.decrypt_file(task) for task in tasks]
        service.close()

        self.assertTrue(all(outcome.success for outcome in outcomes))
        self.assertEqual(FakeSubprocess.created, ["notepad"])   # 3 个文件只启动一次进程
        self.assertTrue(all(proc.returncode is not None for proc in FakeSubprocess.processes))

    def test_suffix_switch_closes_previous_group_process(self):
        FakeSubprocess.OUTPUTS["notepad"] = PLAINTEXT
        service = self._service(queue=("notepad",))
        docx_source = self._make_source(name="一.docx")
        pdf_source = self._make_source(name="二.pdf")

        service.decrypt_file(self._task(docx_source, ".docx"))
        service.decrypt_file(self._task(pdf_source, ".pdf"))

        self.assertEqual(FakeSubprocess.created, ["notepad", "notepad"])  # 新分组重新启动
        self.assertIsNotNone(FakeSubprocess.processes[0].returncode)      # 切组时旧进程已关闭
        self.assertIsNone(FakeSubprocess.processes[1].returncode)         # 新分组进程仍常驻

        service.close()
        self.assertIsNotNone(FakeSubprocess.processes[1].returncode)

    def test_close_without_started_sessions_is_safe(self):
        service = self._service()
        service.close()
        service.close()
        self.assertEqual(FakeSubprocess.created, [])


if __name__ == "__main__":
    unittest.main()
