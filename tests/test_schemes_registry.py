"""方案注册链架构测试：装配顺序、三态契约与「新厂商最小接入」路径。

覆盖三条接入通道：

1. 标识串厂商（零代码）：config ``cipher_markers`` 追加即生效；
2. 结构方案厂商（一个类 + @register）：偏移魔数接入，不动核心；
3. 新文件格式（一行数据）：``generic.add_signature``。

并验证优先级/弃权语义：强证据（格式签名命中）先于弱证据（ECB 指纹）定案，
配置坏条目只跳过不炸链。

保持 Python 3.8 兼容（Win7 构建链同样运行本测试）。
"""

from __future__ import annotations

import unittest

from dlp_pass.config import AppConfig
from dlp_pass.core import formats
from dlp_pass.core.schemes import (
    Decision,
    HeadProbe,
    Scheme,
    SchemeRegistry,
    Verdict,
    default_registry,
    registry_from_config,
)
from dlp_pass.core.schemes import base as schemes_base
from dlp_pass.core.schemes import generic

MARKER = "%TSD-Header-###%"
ATS_BLOCK_A = b"\xe8\xb5\xef\xcc\xa5\x77\x30\x93"
ATS_BLOCK_B = b"\x8b\x51\x46\x7b\x81\x8d\x3e\x89"


def ats_cipher(size: int = 16384) -> bytes:
    data = bytearray()
    while len(data) < size:
        data += ATS_BLOCK_A if (len(data) // 16) % 2 else ATS_BLOCK_B
    return bytes(data[:size])


class DefaultChainTest(unittest.TestCase):
    """默认判定链与旧 formats 门面行为等价。"""

    def setUp(self):
        self.registry = default_registry()

    def test_marker_cipher(self):
        verdict = self.registry.decide(".docx", (MARKER + "x").encode("ascii") + b"\x00" * 40)
        self.assertIs(verdict.decision, Decision.CIPHER)
        self.assertEqual(verdict.scheme, "marker")

    def test_known_signature_plain(self):
        verdict = self.registry.decide(".docx", b"PK\x03\x04" + b"\x00" * 300)
        self.assertIs(verdict.decision, Decision.PLAIN)
        self.assertEqual(verdict.scheme, "format-signature")

    def test_unknown_inconclusive(self):
        rnd = bytes((i * 37 + 11) % 251 ^ (i % 7) for i in range(8192))
        self.assertIsNone(self.registry.is_cipher(".bin", rnd, len(rnd)))

    def test_facade_equivalence(self):
        self.assertIs(formats.is_cipher_content(ats_cipher(), ".xlsx"), True)
        self.assertIs(formats.is_cipher_content(b"PK\x03\x04" + b"\x00" * 300, ".xlsx"), False)

    def test_scheme_order(self):
        names = [s.name for s in self.registry.schemes]
        self.assertEqual(names, ["marker", "format-signature", "ecb-repetition"])


class ConfigMarkerOnboardingTest(unittest.TestCase):
    """通道 1：新标识串厂商 = config 加一行，零代码。"""

    def test_new_marker_string_shorthand(self):
        config = AppConfig()
        config.cipher_markers = (MARKER, "<NEW-DLP-ENC>")
        registry = registry_from_config(config)
        hit = registry.decide(".bin", b"\x9f\x00\xff" + b"<NEW-DLP-ENC>" + b"\x00" * 8)
        self.assertIs(hit.decision, Decision.CIPHER)
        miss = registry.decide(".docx", b"PK\x03\x04" + b"\x00" * 300)
        self.assertIs(miss.decision, Decision.PLAIN)

    def test_new_marker_dict_with_window(self):
        config = AppConfig()
        config.cipher_markers = ({"name": "vendor-y", "token": "XYENC2", "window": 16},)
        registry = registry_from_config(config)
        within = registry.decide(".bin", b"\x00" * 10 + b"XYENC2")
        self.assertIs(within.decision, Decision.CIPHER)
        beyond = registry.decide(".bin", b"\x00" * 40 + b"XYENC2")
        self.assertIs(beyond.decision, Decision.ABSTAIN)  # 窗口外不命中，链继续

    def test_invalid_entries_skipped(self):
        config = AppConfig()
        config.cipher_markers = ({"no_token": 1}, 5, MARKER)
        registry = registry_from_config(config)  # 不抛异常
        self.assertIs(
            registry.decide(".bin", (MARKER + "z").encode("ascii") + b"\x00" * 8).decision,
            Decision.CIPHER,
        )

    def test_empty_markers_fall_back_to_legacy_field(self):
        config = AppConfig()
        config.encryption_marker = "%OLD-ONLY%"
        registry = registry_from_config(config)
        self.assertIs(
            registry.decide(".bin", b"%OLD-ONLY%" + b"\x00" * 8).decision, Decision.CIPHER
        )


class StructuralVendorOnboardingTest(unittest.TestCase):
    """通道 2：固定二进制结构厂商 = 一个 Scheme 子类 + @register。"""

    MAGIC = b"\x58\x46\x4f\x52\x43\x45\x01\x00"   # 虚构厂商偏移 0x200 魔数

    def setUp(self):
        self._saved = list(schemes_base._VENDOR_SCHEME_TYPES)
        registry_mod = schemes_base

        @registry_mod.register
        class VendorZScheme(Scheme):
            name = "vendor-z"
            priority = 20

            def detect(self, probe: HeadProbe) -> Verdict:
                if probe.head[0x200:0x208] == StructuralVendorOnboardingTest.MAGIC:
                    return self.cipher("0x200 结构魔数命中")
                return self.abstain()

    def tearDown(self):
        schemes_base._VENDOR_SCHEME_TYPES[:] = self._saved

    def _sample(self) -> bytes:
        filler = bytearray(ats_cipher(0x1000))
        filler[0x200:0x208] = self.MAGIC
        return bytes(filler)

    def test_vendor_scheme_registered_and_preempts_generic(self):
        registry = default_registry()
        verdict = registry.decide(".bin", self._sample(), 4096)
        self.assertIs(verdict.decision, Decision.CIPHER)
        self.assertEqual(verdict.scheme, "vendor-z")

    def test_vendor_plain_verdict_beats_ecb_fingerprint(self):
        # 强证据定案原则：厂商方案判定 PLAIN 后，弱证据（ECB）不再触发
        registry = default_registry()
        registry.register(self._plain_overrider())
        self.assertIs(registry.decide(".bin", ats_cipher()).decision, Decision.PLAIN)

    @staticmethod
    def _plain_overrider():
        class TrustedOverride(Scheme):
            name = "trusted-marker"
            priority = 5

            def detect(self, probe):
                return Verdict(Decision.PLAIN, self.name, "演示：厂商可信标记")
        return TrustedOverride()


class NewFormatOnboardingTest(unittest.TestCase):
    """通道 3：新文件格式 = add_signature 一行数据。"""

    def setUp(self):
        generic.add_signature(".zbrz", (0, b"\x00\x00\x01\x00"))  # 虚构格式示例

    def tearDown(self):
        generic.PLAINTEXT_SIGNATURES.pop(".zbrz", None)

    def test_new_format_detected_via_generic_layer(self):
        registry = default_registry()
        cipher = registry.decide(".zbrz", ats_cipher(4096))
        self.assertIs(cipher.decision, Decision.CIPHER)
        self.assertEqual(cipher.scheme, "format-signature")
        plain = registry.decide(".zbrz", b"\x00\x00\x01\x00" + ats_cipher(4096)[4:])
        self.assertIs(plain.decision, Decision.PLAIN)


class RegistryMechanicsTest(unittest.TestCase):
    """注册表本身的语义。"""

    def test_default_verdict_configurable(self):
        strict = SchemeRegistry(default_verdict=Decision.CIPHER)
        self.assertIs(strict.decide(".bin", b"\x01\x02").decision, Decision.CIPHER)

    def test_all_abstain_maps_to_none_contract(self):
        self.assertIsNone(default_registry().is_cipher(".unknownext", b"\x01\x02", 2))

    def test_priority_sorting_on_register(self):
        registry = SchemeRegistry()
        late = type("Late", (Scheme,), {"name": "late", "priority": 90,
                                       "detect": lambda self, probe: self.plain("x")})()
        early = type("Early", (Scheme,), {"name": "early", "priority": 1,
                                          "detect": lambda self, probe: self.abstain()})()
        registry.register(late)
        registry.register(early)
        self.assertEqual([s.name for s in registry.schemes], ["early", "late"])


if __name__ == "__main__":
    unittest.main()
