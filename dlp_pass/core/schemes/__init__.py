"""密文判定方案包：多厂商、多机制的可扩展判定链。

出厂判定链（优先级越小越先定案）：

=====  ==========================================  ====================
 10     markers.MarkerScheme                        config.json 标识串
 20     structural.*（厂商结构方案，@register 装配）  偏移魔数/固定头部
 30     generic.FormatSignatureScheme               后缀应有明文签名
 40     generic.EcbRepetitionScheme                 非零重复块统计指纹
=====  ==========================================  ====================

全部弃权时返回 ``ABSTAIN``（三态契约的 ``None``）：由 SourceReader 按明文
放行、由界面提示「无法判定（将按密文处理）」，与既有行为一致。

新厂商接入速查
--------------
1. 带明文标识串      → config.json ``cipher_markers`` 加一行，零代码；
2. 固定二进制结构    → structural.py 增加一个 Scheme 子类 + ``@register``；
3. 新文件格式        → generic.add_signature(".后缀", (偏移, 魔数)) 一行；
4. 透明加密但无标识  → 通常已被 20/30/40 覆盖，无需接入。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import (
    Decision,
    HeadProbe,
    Scheme,
    SchemeRegistry,
    Verdict,
    register,
    vendor_scheme_instances,
)
from .generic import (
    HEAD_WINDOW,
    PLAINTEXT_SIGNATURES,
    EcbRepetitionScheme,
    FormatSignatureScheme,
    add_signature,
    known_suffix,
)
from .markers import DEFAULT_MARKER, DEFAULT_WINDOW, MarkerRule, MarkerScheme

from . import structural  # noqa: F401  导入以触发厂商结构方案的 @register 装配

if TYPE_CHECKING:  # 仅类型标注，运行期避免 core -> config 反向依赖
    from ...config import AppConfig


def default_registry(markers: object = None, default_verdict: Decision = Decision.ABSTAIN) -> SchemeRegistry:
    """构建标准判定链。

    :param markers: 标识串规则（str / dict / MarkerRule 混合列表）；
        ``None`` 时仅使用 :data:`DEFAULT_MARKER`（旧版单标识串行为）。
    """
    rules = []
    for raw in (markers or (DEFAULT_MARKER,)):
        try:
            rules.append(MarkerRule.coerce(raw))
        except (ValueError, TypeError):
            logging.getLogger(__name__).warning("cipher_markers 条目无效，已跳过: %r", raw)
    registry = SchemeRegistry(default_verdict=default_verdict)
    registry.register(MarkerScheme(tuple(rules)))
    for scheme in vendor_scheme_instances():
        registry.register(scheme)
    registry.register(FormatSignatureScheme())
    registry.register(EcbRepetitionScheme())
    return registry


def registry_from_config(config: "AppConfig") -> SchemeRegistry:
    """按应用配置装配判定链（cipher_markers 优先，旧 encryption_marker 兜底）。"""
    return default_registry(markers=config.marker_rules())
