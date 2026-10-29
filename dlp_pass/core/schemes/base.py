"""密文判定方案基础设施：Decision / Verdict / HeadProbe / Scheme / SchemeRegistry。

职责边界
--------
- 方案（:class:`Scheme`）只回答「这段磁盘头部字节是不是密文」，
  不关心字节从哪个进程视角读来（当前进程直读 / PowerShell 非白名单视角 /
  委派白名单进程），也不关心解密与落盘方式；
- 注册表（:class:`SchemeRegistry`）按优先级串联方案，第一个给出
  非 ``ABSTAIN`` 结论的方案定案——高置信的厂商方案在前，
  通用兜底策略在后，避免弱证据（统计指纹）覆盖强证据（格式签名）。

新厂商接入方式（见 ``dlp_pass/core/schemes/__init__.py`` 模块文档）。

保持 Python 3.8 兼容。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional


class Decision(Enum):
    """单个方案对一份内容的判定结论。"""

    CIPHER = "cipher"      # 判定为密文
    PLAIN = "plain"        # 判定为明文（强证据，如格式签名命中）
    ABSTAIN = "abstain"    # 本方案无权判定，交由下一方案


@dataclass(frozen=True)
class Verdict:
    """判定结论及其来源（来源用于日志与远程诊断）。"""

    decision: Decision
    scheme: str
    detail: str = ""

    @property
    def is_cipher(self) -> Optional[bool]:
        """映射到既有三态契约：``True`` 密文 / ``False`` 明文 / ``None`` 无法判定。"""
        if self.decision is Decision.CIPHER:
            return True
        if self.decision is Decision.PLAIN:
            return False
        return None


@dataclass(frozen=True)
class HeadProbe:
    """方案可消费的检测上下文（原始头部字节，非白名单视角）。"""

    suffix: str
    head: bytes
    full_size: int = -1    # 未知时传 -1


class Scheme:
    """判定方案基类：子类实现 :meth:`detect`，声明 ``name`` 与 ``priority``。

    约定：``detect`` 必须对不属于自己管辖的输入返回 :meth:`abstain`，
    而不是错误的 CIPHER/PLAIN——弃权是本架构可无限扩展的前提。
    """

    name = "scheme"
    priority = 100

    def abstain(self, detail: str = "") -> Verdict:
        return Verdict(Decision.ABSTAIN, self.name, detail)

    def cipher(self, detail: str) -> Verdict:
        return Verdict(Decision.CIPHER, self.name, detail)

    def plain(self, detail: str) -> Verdict:
        return Verdict(Decision.PLAIN, self.name, detail)

    def detect(self, probe: HeadProbe) -> Verdict:  # pragma: no cover - 抽象
        raise NotImplementedError


# 结构性厂商方案的登记表：structural.py 中的方案通过 @register 收集，
# 由 default_registry() 装配进判定链（避免包级循环导入）。
_VENDOR_SCHEME_TYPES: list[type] = []


def register(cls: type) -> type:
    """类装饰器：把结构性厂商方案纳入默认注册表装配。"""
    _VENDOR_SCHEME_TYPES.append(cls)
    return cls


def vendor_scheme_instances() -> list[Scheme]:
    return [cls() for cls in _VENDOR_SCHEME_TYPES]


class SchemeRegistry:
    """按优先级串联方案的判定链。"""

    def __init__(self, schemes: Iterable[Scheme] = (), default_verdict: Decision = Decision.ABSTAIN):
        self._schemes: list[Scheme] = []
        self.default_verdict = default_verdict
        for scheme in schemes:
            self.register(scheme)

    def register(self, scheme: Scheme) -> Scheme:
        """登记方案并保持按 ``priority`` 升序执行；返回方案本身便于装饰式用法。"""
        self._schemes.append(scheme)
        self._schemes.sort(key=lambda s: s.priority)
        return scheme

    @property
    def schemes(self) -> tuple[Scheme, ...]:
        return tuple(self._schemes)

    def decide(self, suffix: str, head: bytes, full_size: int = -1) -> Verdict:
        probe = HeadProbe(suffix=suffix, head=head, full_size=full_size)
        for scheme in self._schemes:
            verdict = scheme.detect(probe)
            if verdict.decision is not Decision.ABSTAIN:
                return verdict
        return Verdict(self.default_verdict, "default")

    def is_cipher(self, suffix: str, head: bytes, full_size: int = -1) -> Optional[bool]:
        """:meth:`decide` 的三态布尔快捷方式（与旧契约一致）。"""
        return self.decide(suffix, head, full_size).is_cipher
