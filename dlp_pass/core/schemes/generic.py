"""厂商无关的通用兜底判定策略。

- :class:`FormatSignatureScheme`：已知后缀应有「明文签名」（魔数），
  磁盘字节缺失签名且头部无文本特征 → 密文；签名命中 → 明文。
  这是透明加密（亿赛通以外的艾特盾、IP-Guard、绿盾等大多无明文标识串）
  最可靠的通用判据；新格式的签名通过 :func:`add_signature` 数据行接入。
- :class:`EcbRepetitionScheme`：分组密码无链式模式（ECB 风格）时，
  空白/模板文件暴露大量「相同的非零密文块」；统计指纹仅作未知后缀的
  弱证据，优先级最低（艾特盾空白样本实测同一 8 字节块重复 60~366 次）。

保持 Python 3.8 兼容。
"""

from __future__ import annotations

import collections
from typing import Dict, Sequence, Tuple

from .base import HeadProbe, Scheme, Verdict

# 检测器读取头部的推荐窗口（verifier 的 PowerShell 协议按此长度回传）
HEAD_WINDOW = 16384

# 明文签名注册表：后缀 -> 可接受的 (偏移, 魔数) 列表（任一匹配即为明文）
PLAINTEXT_SIGNATURES: Dict[str, Tuple[Tuple[int, bytes], ...]] = {}


def add_signature(suffix: str, *magics: Tuple[int, bytes]) -> None:
    """登记某后缀可接受的明文签名（新格式接入 = 一行数据，不改判定逻辑）。"""
    key = suffix.lower()
    PLAINTEXT_SIGNATURES[key] = PLAINTEXT_SIGNATURES.get(key, ()) + magics


def _register(magics: Sequence[Tuple[int, bytes]], *suffixes: str) -> None:
    for suffix in suffixes:
        add_signature(suffix, *magics)


# -- OLE 复合文档（.doc 额外接受 Word 6/95 独立二进制格式）
_OLE = ((0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),)
_register(_OLE, ".xls", ".ppt", ".msi", ".msp", ".pub", ".xla",
          ".pps", ".wps", ".et", ".dps", ".sldprt", ".sldasm", ".slddrw",
          ".rvt", ".rfa", ".max")
_register(_OLE + ((0, b"\x1b\xa5"),), ".doc")

# -- ZIP 家族（OOXML / ODF / 普通 zip）：空包 PK\\x05\\x06、跨卷 PK\\x07\\x08 同样合法
_ZIP = ((0, b"PK\x03\x04"), (0, b"PK\x05\x06"), (0, b"PK\x07\x08"))
_register(_ZIP, ".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm",
          ".zip", ".odt", ".ods", ".odp", ".epub", ".vsdx", ".dwfx")

# -- 其他常见单头签名格式
_register(((0, b"%PDF"),), ".pdf")
_register(((0, b"AC1"),), ".dwg")                # AutoCAD：AC10xx 版本串
_register(((0, b"BLENDER"),), ".blend")
_register(((0, b"Rar!\x1a\x07"),), ".rar")
_register(((0, b"7z\xbc\xaf\x27\x1c"),), ".7z")
_register(((0, b"\x1f\x8b"),), ".gz")
_register(((0, b"BZh"),), ".bz2")
_register(((0, b"\xfd7zXZ\x00"),), ".xz")
_register(((0, b"MSC\x00"),), ".cab")
_register(((0, b"\x89PNG\r\n\x1a\n"),), ".png")
_register(((0, b"\xff\xd8\xff"),), ".jpg", ".jpeg")
_register(((0, b"GIF8"),), ".gif")
_register(((0, b"BM"),), ".bmp")
_register(((0, b"II*\x00"), (0, b"MM\x00*")), ".tif", ".tiff")
_register(((0, b"8BPS"),), ".psd")
_register(((0, b"MZ"),), ".exe", ".dll", ".ocx", ".sys")
_register(((0, b"\x4c\x00\x00\x00\x01\x14\x02\x00"),), ".lnk")

# 密文头部出现大比例可打印 ASCII 的概率极低：据此为「签名不匹配」兜底
# 防误报（另存为 HTML/RTF/XML 但保留 Office 后缀的明文文件）。
_TEXT_HEAD_BYTES = 32
_TEXT_MIN_RATIO = 0.75   # 头部至少 75% 为文本字符才认定呈文本特征


def _looks_like_text(head: bytes) -> bool:
    if not head:
        return False
    if head[:2] in (b"\xff\xfe", b"\xfe\xff") or head[:3] == b"\xef\xbb\xbf":
        return True  # BOM
    textish = sum(1 for b in head if 32 <= b < 127 or b in (9, 10, 13))
    return textish >= len(head) * _TEXT_MIN_RATIO


def known_suffix(suffix: str) -> bool:
    """该后缀是否有明文签名可查。"""
    return suffix.lower() in PLAINTEXT_SIGNATURES


class FormatSignatureScheme(Scheme):
    """已知后缀：应有明文签名缺失 → 密文；命中 → 明文；文本头 → 明文（防误报）。"""

    name = "format-signature"
    priority = 30

    def detect(self, probe: HeadProbe) -> Verdict:
        magics = PLAINTEXT_SIGNATURES.get(probe.suffix.lower())
        if magics is None or not probe.head:
            return self.abstain()
        for offset, magic in magics:
            if probe.head[offset:offset + len(magic)] == magic:
                return self.plain("签名命中 0x%X" % offset)
        window = probe.head[:_TEXT_HEAD_BYTES]
        if _looks_like_text(window):
            return self.plain("签名不匹配但头部呈文本特征")
        return self.cipher("缺少 %s 应有的明文签名（头 8 字节 %s）" % (
            probe.suffix or "该后缀", window[:8].hex().upper() or "空"))


# -- ECB 重复块指纹 ----------------------------------------------------------
_ECB_BLOCK = 8               # 块粒度（艾特盾样本实测 8 字节块重复）
_ECB_MIN_REPEATS = 32        # 同一非零块最少重复次数


def _ecb_max_repeat(data: bytes) -> int:
    """返回头部窗口内重复次数最高的「非零块」的出现次数。"""
    window = data[:HEAD_WINDOW]
    if len(window) < _ECB_BLOCK * 2:
        return 0
    counter = collections.Counter(
        window[i:i + _ECB_BLOCK] for i in range(0, len(window) - _ECB_BLOCK + 1, _ECB_BLOCK)
    )
    best = 0
    for block, count in counter.items():
        if any(block) and count > best:  # 全零块常见于正常明文填充，不计入指纹
            best = count
    return best


class EcbRepetitionScheme(Scheme):
    """未知后缀的最后防线：ECB 分组重复密文指纹（弱证据，优先级最低）。"""

    name = "ecb-repetition"
    priority = 40

    def detect(self, probe: HeadProbe) -> Verdict:
        if known_suffix(probe.suffix):
            return self.abstain()  # 有签名可查时由 FormatSignatureScheme 定案
        repeats = _ecb_max_repeat(probe.head)
        if repeats >= _ECB_MIN_REPEATS:
            return self.cipher("头部窗口内同一非零 %d 字节块重复 %d 次" % (_ECB_BLOCK, repeats))
        return self.abstain()
