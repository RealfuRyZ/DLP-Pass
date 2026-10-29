"""结构性厂商方案接入模板（有固定魔数 / 偏移 / 尾部签名时用）。

与「标识串方案」（可在 config.json 加一行，零代码）相比，本模块用于
密文有稳定二进制结构、但无明文标识串的情况。新增一个厂商 = 复制一个
类并实现 ``detect``，加 ``@registry_register`` 装饰即接入，核心流程不动::

    from .base import HeadProbe, Scheme, Verdict, register
    @register
    class VendorXScheme(Scheme):
        name = "vendor-x"
        priority = 20                       # 介于标识串(10)与通用签名(30)之间

        def detect(self, probe: HeadProbe) -> Verdict:
            if probe.head[0x200:0x204] == b"\\xXX\\xXX\\xXX\\xXX":
                return self.cipher("vendor-x 偏移 0x200 魔数命中")
            return self.abstain()

艾特盾（issue #1）当前由通用 format-signature + ecb-repetition 两级即可
覆盖；若日后拿到更精确的跨文件稳定结构特征，在此补一个 VendorScheme。

保持 Python 3.8 兼容。
"""

from __future__ import annotations

# 本模块当前不含出厂结构性方案；保留为接入点与文档示例，
# 导入无副作用，确保 ``from . import structural`` 始终可用。
