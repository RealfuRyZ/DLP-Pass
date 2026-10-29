"""（兼容门面）内容级密文判定 —— 实现已迁移至 :mod:`dlp_pass.core.schemes`。

新代码请直接使用 ``schemes.registry_from_config(config)`` /
``schemes.default_registry()``；本模块保留旧的
:func:`is_cipher_content` 三态函数签名，供既有调用方与测试沿用。

模块文档中的背景分析（亿赛通标识串 / 艾特盾无标识串的签名缺失与
ECB 重复块特征）现由 schemes 包的 markers / generic 两级实现。
"""

from __future__ import annotations

from typing import Optional

from .schemes import DEFAULT_MARKER, default_registry
from .schemes.generic import HEAD_WINDOW, known_suffix

# 兼容旧常量名（verifier 旧引用）
ECB_WINDOW = HEAD_WINDOW


def is_cipher_content(
    data: bytes,
    suffix: str,
    marker: str = DEFAULT_MARKER,
) -> Optional[bool]:
    """按文件头内容判断磁盘数据是否为密文（``True``/``False``/``None``）。"""
    return default_registry(markers=(marker,)).is_cipher(suffix, data, len(data))
