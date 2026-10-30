"""内容级密文判定单元测试（formats / SourceReader，不依赖真实 DLP 环境）。

覆盖两类厂商场景与既有行为回归：

1. 亿赛通（带 ``%TSD-Header-###%`` 标识串）：旧行为不变；
2. 艾特盾（无任何明文标识，issue #1）：按「后缀应有明文签名缺失」与
   ECB 重复块指纹判定；
3. 防误报：合法 OLE / ZIP 明文、另存为 HTML/RTF/Word95 的 .doc、
   空白明文、未知后缀随机数据。

保持 Python 3.8 兼容（Win7 构建链同样运行本测试）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from dlp_pass.core import formats
from dlp_pass.core.source_reader import SourceReader, SourceReadError

MARKER = "%TSD-Header-###%"

# 艾特盾空白样本实测密文块（终端密钥相关，仅作确定性构造用）
ATS_BLOCK_A = b"\xe8\xb5\xef\xcc\xa5\x77\x30\x93"
ATS_BLOCK_B = b"\x8b\x51\x46\x7b\x81\x8d\x3e\x89"


def ats_cipher(size: int = 16384, block_size: int = 16) -> bytes:
    """构造艾特盾风格密文：无明文签名 + 同一密文块大量重复。"""
    data = bytearray()
    while len(data) < size:
        data += ATS_BLOCK_A if (len(data) // block_size) % 2 else ATS_BLOCK_B
    return bytes(data[:size])


class IsCipherContentTest(unittest.TestCase):
    """formats.is_cipher_content 判定矩阵。"""

    # -- 亿赛通回归 --------------------------------------------------------
    def test_tsd_marker_detected(self):
        self.assertIs(formats.is_cipher_content(
            (MARKER + "x").encode("ascii") + b"\x00" * 50, ".docx"), True)

    def test_tsd_marker_on_unknown_suffix(self):
        self.assertIs(formats.is_cipher_content(
            MARKER.encode("ascii") + b"\x00" * 100, ".xyz"), True)

    # -- 艾特盾（无标识串） -------------------------------------------------
    def test_ats_office_cipher_detected_by_signature(self):
        for suffix in (".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"):
            self.assertIs(formats.is_cipher_content(ats_cipher(), suffix), True, suffix)

    def test_ats_cipher_detected_by_ecb_fingerprint(self):
        # 未知后缀无签名可查，退化为 ECB 重复块指纹
        self.assertIs(formats.is_cipher_content(ats_cipher(), ".bin"), True)

    # -- 明文与防误报 -------------------------------------------------------
    def test_plaintext_signatures(self):
        self.assertIs(formats.is_cipher_content(
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 300, ".xls"), False)
        self.assertIs(formats.is_cipher_content(b"PK\x03\x04" + b"\x00" * 300, ".docx"), False)

    def test_alternative_saved_docs_not_cipher(self):
        # 「另存为」HTML / RTF 保留 .doc 后缀的明文不应误报
        self.assertIs(formats.is_cipher_content(
            b'<html xmlns:o="urn:schemas"><body>x' * 20, ".doc"), False)
        self.assertIs(formats.is_cipher_content(
            b"{\\rtf1\\ansi hello" * 10, ".doc"), False)

    def test_word95_doc_not_cipher(self):
        self.assertIs(formats.is_cipher_content(
            b"\x1b\xa5\x00\x02" + b"\x00" * 30, ".doc"), False)

    def test_random_unknown_suffix_inconclusive(self):
        rnd = bytes((i * 37 + 11) % 251 ^ (i % 7) for i in range(8192))
        self.assertIsNone(formats.is_cipher_content(rnd, ".bin"))

    def test_empty_file_inconclusive(self):
        self.assertIsNone(formats.is_cipher_content(b"", ".docx"))
        self.assertIsNone(formats.is_cipher_content(b"", ".bin"))

    def test_marker_window_beyond_64_bytes_not_matched(self):
        payload = b"\x00" * 70 + MARKER.encode("ascii")
        self.assertFalse(formats.is_cipher_content(payload, ".bin") is True)


class SourceReaderTest(unittest.TestCase):
    """SourceReader：明文直返、密文委派、委派失败报错。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlp-reader-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.reader = SourceReader(MARKER)

    def _write(self, name: str, data: bytes) -> Path:
        path = self.tmp / name
        path.write_bytes(data)
        return path

    def test_plaintext_returned_directly(self):
        path = self._write("报告.docx", b"PK\x03\x04" + os.urandom(64))
        self.assertEqual(self.reader.read(path), path.read_bytes())

    def test_tsd_ciphertext_delegates(self):
        plain = b"DECRYPTED DOCX"
        path = self._write("t.docx", (MARKER + "pad").encode("ascii") + b"\x00" * 40)
        self.reader._read_via_cmd = lambda p: plain  # type: ignore[assignment]
        self.assertEqual(self.reader.read(path), plain)

    def test_ats_cipher_delegates(self):
        plain = b"PK\x03\x04" + b"REAL OOXML"
        path = self._write("blank.docx", ats_cipher())
        self.reader._read_via_cmd = lambda p: plain  # type: ignore[assignment]
        self.assertEqual(self.reader.read(path), plain)

    def test_delegation_still_cipher_raises(self):
        path = self._write("blank.xlsx", ats_cipher())
        self.reader._read_via_cmd = lambda p: ats_cipher(2048)  # type: ignore[assignment]
        self.reader._read_via_python = lambda p: ats_cipher(2048)  # type: ignore[assignment]
        with self.assertRaises(SourceReadError):
            self.reader.read(path)


class _FakeSession:
    """伪装进程会话替身：read_file 返回预置字节（模拟驱动解密视角）。"""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def read_file(self, path):
        self.calls.append(path)
        return self.payload


class DisguisedReadDelegationTest(unittest.TestCase):
    """v2.5.1（issue #1）：伪装白名单进程读取优先于 cmd / python。"""

    PLAIN = b"PK\x03\x04" + b"REAL OOXML PAYLOAD"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlp-reader-dg-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.reader = SourceReader(MARKER)

    def _write(self, name, data):
        path = self.tmp / name
        path.write_bytes(data)
        return path

    def test_disguised_session_read_preferred(self):
        path = self._write("blank.docx", ats_cipher())
        session = _FakeSession(self.PLAIN)

        def _boom(_p):
            raise AssertionError("伪装进程拿到明文后不应再走 cmd")

        self.reader._read_via_cmd = _boom  # type: ignore[assignment]
        data = self.reader.read(
            path, process_queue=("winword",), acquire_session=lambda name: session
        )
        self.assertEqual(data, self.PLAIN)
        self.assertEqual(session.calls, [path])

    def test_disguised_cipher_falls_back_to_cmd(self):
        path = self._write("blank.docx", ats_cipher())
        session = _FakeSession(ats_cipher(2048))
        self.reader._read_via_cmd = lambda p: self.PLAIN  # type: ignore[assignment]
        data = self.reader.read(
            path, process_queue=("winword",), acquire_session=lambda name: session
        )
        self.assertEqual(data, self.PLAIN)

    def test_session_unavailable_skips_disguised(self):
        path = self._write("blank.docx", ats_cipher())
        self.reader._read_via_cmd = lambda p: self.PLAIN  # type: ignore[assignment]
        data = self.reader.read(
            path, process_queue=("winword",), acquire_session=lambda name: None
        )
        self.assertEqual(data, self.PLAIN)

    def test_identical_ciphertext_error_explains_no_authorization(self):
        path = self._write("blank.xlsx", ats_cipher())
        disk = path.read_bytes()
        session = _FakeSession(disk)  # 伪装进程读回与磁盘一致的密文（未授权）
        self.reader._read_via_cmd = lambda p: disk  # type: ignore[assignment]
        self.reader._read_via_python = lambda p: None  # type: ignore[assignment]
        with self.assertRaises(SourceReadError) as ctx:
            self.reader.read(
                path, process_queue=("excel",), acquire_session=lambda name: session
            )
        message = str(ctx.exception)
        self.assertIn("与磁盘密文完全一致", message)
        self.assertIn("未对这些进程授权", message)

    def test_no_data_error_keeps_generic_message(self):
        path = self._write("blank.xlsx", ats_cipher())
        self.reader._read_via_cmd = lambda p: None  # type: ignore[assignment]
        self.reader._read_via_python = lambda p: None  # type: ignore[assignment]
        with self.assertRaises(SourceReadError) as ctx:
            self.reader.read(path)
        self.assertIn("委派读取均失败", str(ctx.exception))


@unittest.skipUnless(shutil.which("powershell"), "需要 PowerShell 环境")
class VerifierIntegrationTest(unittest.TestCase):
    """EncryptionVerifier：PowerShell 头部字节协议 + 格式判定端到端。"""

    def setUp(self):
        from dlp_pass.config import AppConfig
        from dlp_pass.core.verifier import EncryptionVerifier

        self.verifier = EncryptionVerifier(AppConfig())
        self.addCleanup(self.verifier.close)
        self.tmp = Path(tempfile.mkdtemp(prefix="dlp-verifier-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _check(self, name: str, data: bytes):
        path = self.tmp / name
        path.write_bytes(data)
        result = self.verifier.is_encrypted(path)
        if result is None:  # 常驻与一次性读取均失败时不误判
            self.skipTest("PowerShell 读取头部字节失败，跳过环境相关断言")
        return result

    def test_ats_cipher_detected(self):
        self.assertIs(self._check("空白.docx", ats_cipher()), True)

    def test_tsd_cipher_detected(self):
        self.assertIs(
            self._check("t.xlsx", (MARKER + "x").encode("ascii") + ats_cipher(4096)), True)

    def test_plaintext_not_detected(self):
        self.assertIs(self._check("ok.docx", b"PK\x03\x04" + os.urandom(64)), False)


class PassSuffixTest(unittest.TestCase):
    """原地解密校验对象的 .pass 临时后缀必须剥离为真实后缀（v2.4.2 缺陷回归）。

    合法明文空白 .xls（BIFF 记录）含大量重复非零 8 字节块；若按 `.pass`
    判定则格式签名方案失效、误落 ECB 统计指纹 → 明文被误判密文 → 原地解密
    全量失败。剥离后缀后由 OLE 强证据定案为明文。
    """

    def test_effective_suffix_stripping(self):
        from dlp_pass.core.verifier import _effective_suffix

        self.assertEqual(_effective_suffix(Path("报告.xls.pass")), ".xls")
        self.assertEqual(_effective_suffix(Path("a.b.docx.pass")), ".docx")
        self.assertEqual(_effective_suffix(Path("notes.pass")), ".pass")   # 无内层后缀保持原样
        self.assertEqual(_effective_suffix(Path("normal.txt")), ".txt")

    @unittest.skipUnless(shutil.which("powershell"), "需要 PowerShell 环境")
    def test_plaintext_xls_pass_not_misjudged(self):
        from dlp_pass.config import AppConfig
        from dlp_pass.core.verifier import EncryptionVerifier

        tmp = Path(tempfile.mkdtemp(prefix="dlp-pass-suffix-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        verifier = EncryptionVerifier(AppConfig())
        try:
            # 合法明文 .xls：OLE 头 + 大量重复非零记录块（空白 BIFF 模板形态）
            xls = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00\xff\x01\x22\x33\x44\x55\x66" * 200
            (tmp / "水浸传感器.xls.pass").write_bytes(xls)
            self.assertIs(verifier.is_encrypted(tmp / "水浸传感器.xls.pass"), False)
            # 合法明文扫描 PDF：%PDF 头 + 大量重复图像行模式（issue 现场 54~245 次重复形态）
            pdf = b"%PDF-1.6\n" + b"\x00\x00\xff\xdc\xdc\x00\x00\x00" * 183
            (tmp / "投标邀请函.pdf.pass").write_bytes(pdf)
            self.assertIs(verifier.is_encrypted(tmp / "投标邀请函.pdf.pass"), False)
        finally:
            verifier.close()


if __name__ == "__main__":
    unittest.main()
