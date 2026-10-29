"""标识串型密文方案（数据驱动）：头部窗口内出现厂商明文标识串 → 密文。

亿赛通 CDGC 的 ``%TSD-Header-###%`` 属于此类；后续任何「带明文标识串」
的新厂商只需在 ``config.json`` 的 ``cipher_markers`` 增加一项，
**无需改动任何代码**::

    {
        "cipher_markers": [
            "%TSD-Header-###%",
            {"name": "某新厂商", "token": "<NEW-ENC>", "window": 128}
        ]
    }

保持 Python 3.8 兼容。
"""

from __future__ import annotations

from typing import Iterable, Sequence, Tuple

from .base import HeadProbe, Scheme, Verdict

DEFAULT_MARKER = "%TSD-Header-###%"
DEFAULT_WINDOW = 64


class MarkerRule:
    """一条标识串规则：``(name, token, window)``。"""

    __slots__ = ("name", "token", "window")

    def __init__(self, name: str, token: str, window: int = DEFAULT_WINDOW):
        self.name = name
        self.token = token
        self.window = max(int(window), 0)

    @classmethod
    def coerce(cls, raw: object) -> "MarkerRule":
        """接受 JSON 里的字符串简写或 {name, token, window} 对象。"""
        if isinstance(raw, str):
            return cls("marker", raw)
        if isinstance(raw, dict):
            token = str(raw.get("token", ""))
            if not token:
                raise ValueError("cipher_markers 条目缺少 token: %r" % (raw,))
            return cls(str(raw.get("name", "marker")), token, int(raw.get("window", DEFAULT_WINDOW)))
        raise ValueError("cipher_markers 条目格式无效: %r" % (raw,))

    def as_tuple(self) -> Tuple[str, bytes, int]:
        return self.name, self.token.encode("ascii", "ignore"), self.window


class MarkerScheme(Scheme):
    """头部窗口内命中任一标识串 → 密文；否则弃权（标识串只证明存在，不证明不存在）。"""

    name = "marker"
    priority = 10

    def __init__(self, rules: Iterable[object] = ()):
        self._rules = tuple(self._normalize(rules))

    @staticmethod
    def _normalize(rules: Iterable[object]) -> Sequence[Tuple[str, bytes, int]]:
        normalized = []
        for raw in rules:
            rule = raw.as_tuple() if isinstance(raw, MarkerRule) else MarkerRule.coerce(raw).as_tuple()
            if rule[1]:
                normalized.append(rule)
        return normalized

    def detect(self, probe: HeadProbe) -> Verdict:
        for name, token, window in self._rules:
            if token and token in probe.head[:window]:
                return self.cipher("命中标识串 %s" % name)
        return self.abstain()
