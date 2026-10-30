"""packaging/writer_stub.py 批处理协议集成测试（真实子进程，不依赖 DLP 环境）。

覆盖 v3 协议（v2.5.1，issue #1）：
- 写请求 ``<base64 路径> <字节数>`` + 数据 -> ``OK``；
- 读请求 ``<base64 路径> -1`` -> ``OK <n>`` + n 字节内容；
- 读不存在文件 -> ``ERR <base64 消息>``；
- stdin 关闭（EOF）后进程正常退出。

保持 Python 3.8 兼容（Win7 构建链同样运行本测试）。
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_STUB = Path(__file__).resolve().parents[1] / "packaging" / "writer_stub.py"
_CREATE_NO_WINDOW = 0x08000000


def _header(path: Path, size: int) -> bytes:
    return base64.b64encode(os.fsencode(str(path))) + (" %d\n" % size).encode("ascii")


def _read_exact(stream: object, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)  # type: ignore[attr-defined]
        if not chunk:
            raise AssertionError("stub 输出在数据读取完成前中断")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class WriterStubBatchProtocolTest(unittest.TestCase):
    """常驻批处理模式：写 -> 读 -> EOF 退出的完整往返。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlp-stub-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proc = subprocess.Popen(
            [sys.executable, str(_STUB)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=5)
        for stream in (self.proc.stdin, self.proc.stdout):
            if stream is not None and not stream.closed:
                stream.close()

    def _request(self, data: bytes) -> bytes:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(data)
        self.proc.stdin.flush()
        return self.proc.stdout.readline().strip()

    def test_write_then_read_roundtrip(self):
        target = self.tmp / "导出.docx.pass"
        payload = b"PK\x03\x04" + os.urandom(4096)

        reply = self._request(_header(target, len(payload)) + payload)
        self.assertEqual(reply, b"OK")
        self.assertEqual(target.read_bytes(), payload)

        reply = self._request(_header(target, -1))
        self.assertTrue(reply.startswith(b"OK "), reply)
        size = int(reply.split()[1])
        self.assertEqual(size, len(payload))
        self.assertEqual(_read_exact(self.proc.stdout, size), payload)

    def test_read_missing_file_returns_err(self):
        missing = self.tmp / "不存在.docx"
        reply = self._request(_header(missing, -1))
        self.assertTrue(reply.startswith(b"ERR "), reply)

    def test_multiple_requests_serial(self):
        for index in range(3):
            target = self.tmp / ("f%d.bin" % index)
            payload = bytes([index]) * (index + 1) * 100
            self.assertEqual(self._request(_header(target, len(payload)) + payload), b"OK")
            reply = self._request(_header(target, -1))
            size = int(reply.split()[1])
            self.assertEqual(_read_exact(self.proc.stdout, size), payload)

    def test_stdin_eof_exits_cleanly(self):
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        try:
            self.assertEqual(self.proc.wait(timeout=5), 0)
        except subprocess.TimeoutExpired:
            self.fail("stdin 关闭后 stub 进程未在 5 秒内退出")


if __name__ == "__main__":
    unittest.main()
