"""内容级密文判定：不依赖单一密文标识串，按文件格式识别「磁盘密文」。

背景
----
不同 DLP 厂商的透明加密容器差异很大：

- 亿赛通 CDGC：密文头部带明文标识串 ``%TSD-Header-###%``，按标识串即可判定；
- 艾特盾 DLP（issue #1）：全盘密文 **不含任何明文标识**（文件头 / 文件尾 /
  属性流均无签名串），也无法按固定魔数表匹配——密文头随终端密钥变化。
  但存在稳定的可判定特征：
  1. 受管 Office 文档的磁盘密文必然缺少该格式应有的明文签名
     （``.docx/.xlsx/.pptx`` 不再是 ``PK\\x03\\x04``，``.doc/.xls/.ppt``
     不再是 OLE 头 ``D0CF11E0A1B11AE1``）；
  2. 分组密码以无链式（ECB 风格）方式变换时，空白 / 模板文件会暴露大量
     **相同的非零密文块**（实测艾特盾空白样本前 16KB 内同一 8 字节块
     重复 60~366 次），明文二进制文件极少出现此特征（全零填充块已排除）。

判定规则（:func:`is_cipher_content`）
----------------------------------
1. 前 64 字节内出现密文标识串（默认 ``%TSD-Header-###%``）→ 密文；
2. 后缀在明文签名注册表中：文件头匹配任一签名 → 明文；均不匹配且文件头
   不呈文本特征 → 密文（呈文本特征则视为「另存为 HTML/RTF 的明文」）；
3. 后缀未知：ECB 重复块指纹达到阈值 → 密文；否则无法判定（返回 ``None``，
   调用方按明文放行，与旧版「仅标识串判定」的行为一致）。

注意：本模块只做「磁盘原始字节」视角的判断（非白名单进程读到的内容），
解密本身仍由白名单进程读取 + 伪装进程落盘完成，与厂商密钥无关。

保持 Python 3.8 兼容（仅注解中使用新语法，由 ``__future__`` 支持）。
"""

from __future__ import annotations

import collections
from typing import Dict, Optional, Sequence, Tuple

DEFAULT_MARKER = "%TSD-Header-###%"

# 标识串检测窗口（与前版 verifier 行为一致：前 64 字节内出现即判定密文）
_MARKER_WINDOW = 64

# ECB 重复块指纹（仅用于无签名注册表可查的后缀）
ECB_WINDOW = 16384           # 参与统计的文件头窗口（也是检测器读取头部的推荐窗口）
_ECB_BLOCK = 8               # 块粒度（艾特盾样本实测 8 字节块重复）
_ECB_MIN_REPEATS = 32        # 同一非零块最少重复次数

# 明文签名注册表：后缀 -> 可接受的 (偏移, 魔数) 列表（任一匹配即为明文）
_SIGNATURES: Dict[str, Tuple[Tuple[int, bytes], ...]] = {}


def _register(magic: Sequence[Tuple[int, bytes]], *suffixes: str) -> None:
    for suffix in suffixes:
        _SIGNATURES[suffix] = tuple(magic)


# -- OLE 复合文档（.doc/.xls/.ppt/.msi 等）
# .doc 额外接受 Word 6/95 独立二进制格式（1B A5 开头，非 OLE 容器）
_OLE = ((0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),)
_register(_OLE, ".xls", ".ppt", ".msi", ".msp", ".pub", ".xla",
          ".pps", ".wps", ".et", ".dps", ".sldprt", ".sldasm", ".slddrw",
          ".rvt", ".rfa", ".max")
_register(_OLE + ((0, b"\x1b\xa5"),), ".doc")

# -- ZIP 家族（OOXML / ODF / 普通 zip）：空包 PK\x05\x06、跨卷 PK\x07\x08 同样合法
_ZIP = ((0, b"PK\x03\x04"), (0, b"PK\x05\x06"), (0, b"PK\x07\x08"))
_register(_ZIP, ".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm",
          ".zip", ".odt", ".ods", ".odp", ".epub", ".vsdx", ".dwfx")

# -- 其他常见单头签名格式
_register(((0, b"%PDF"),), ".pdf")
_register(((0, b"AC1"),), ".dwg")          # AutoCAD：AC10xx / AC10xx 版本串
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


def _ecb_max_repeat(data: bytes) -> int:
    """返回头部窗口内重复次数最高的「非零块」的出现次数。"""
    window = data[:ECB_WINDOW]
    if len(window) < _ECB_BLOCK * 2:
        return 0
    counter = collections.Counter(
        window[i:i + _ECB_BLOCK] for i in range(0, len(window) - _ECB_BLOCK + 1, _ECB_BLOCK)
    )
    best = 0
    for block, count in counter.items():
        if any(block):          # 全零块常见于正常明文填充，不计入指纹
            if count > best:
                best = count
    return best


def known_suffix(suffix: str) -> bool:
    """该后缀是否有明文签名可查。"""
    return suffix.lower() in _SIGNATURES


def _matches_signature(data: bytes, magic: Sequence[Tuple[int, bytes]]) -> bool:
    for offset, head in magic:
        if data[offset:offset + len(head)] == head:
            return True
    return False


_TEXT_HEAD_BYTES = 32


def _looks_like_text(data: bytes) -> bool:
    """文件头是否呈文本特征（如「另存为 HTML/RTF/XML 但保留 Office 后缀」的明文）。

    密文头部出现大比例可打印 ASCII 的概率极低，据此为签名不匹配兜底防误报。
    """
    head = data[:_TEXT_HEAD_BYTES]
    if not head:
        return False
    if head[:2] in (b"\xff\xfe", b"\xfe\xff") or head[:3] == b"\xef\xbb\xbf":
        return True  # BOM
    textish = sum(1 for b in head if 32 <= b < 127 or b in (9, 10, 13))
    return textish >= len(head) * 3 // 4


def is_cipher_content(
    data: bytes,
    suffix: str,
    marker: str = DEFAULT_MARKER,
) -> Optional[bool]:
    """按文件头内容判断磁盘数据是否为密文。

    :return: ``True`` 判定为密文；``False`` 判定为明文；``None`` 无法判定
        （未知后缀且无密文特征，调用方应视为明文 / 可信视角）。
    """
    marker_bytes = marker.encode("ascii", "ignore") if marker else b""
    if marker_bytes and marker_bytes in data[:_MARKER_WINDOW]:
        return True

    key = suffix.lower()
    magic = _SIGNATURES.get(key)
    if magic is not None:
        if not data:
            return None
        if _matches_signature(data, magic):
            return False
        # 签名不匹配：密文头文本化（HTML/RTF 另存）与 Word 旧格式均视为明文
        return not _looks_like_text(data)

    if _ecb_max_repeat(data) >= _ECB_MIN_REPEATS:
        return True
    return None
