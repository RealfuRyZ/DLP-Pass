"""应用配置。

集中管理密文标识、超时、缓存目录、默认输出目录与映射目录覆盖等参数。
可通过 ``config.json`` 覆盖默认值（查找顺序：exe 同目录 → 项目根目录），示例：

.. code-block:: json

    {
        "powershell_timeout": 10.0,
        "default_output_dir": "D:/解密输出",
        "mappings_dir": "D:/my-mappings",
        "check_updates": false
    }

核心解密完全离线运行：不连接数据库；映射规则由本地 ``mappings/`` 目录提供
（见 :mod:`dlp_pass.core.mapping_loader`）。唯一的可选网络行为是启动时向
GitHub Release 查询最新版本，可通过 ``check_updates: false`` 关闭。
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProcessRule:
    """某一后缀对应的伪装白名单进程规则（主进程 + 备用进程队列）。"""

    primary: str
    alternatives: tuple[str, ...] = ()

    def as_queue(self) -> list[str]:
        """返回按尝试顺序排列的进程名列表：``[主进程, 备用...]``。"""
        return [self.primary, *self.alternatives]


# 兜底规则：mappings/ 缺失或某后缀无规则时使用（与最初版本行为一致）
DEFAULT_PDF_RULE = ProcessRule("pdfeditor", ("AcroRd32", "FoxitPDFEditor"))
DEFAULT_FALLBACK_RULE = ProcessRule("notepad", ("winword", "wps"))


def _default_output_dir() -> Path:
    desktop = Path.home() / "Desktop"
    base = desktop if desktop.is_dir() else Path.home()
    return base / "DLP解密输出"


def _default_writer_cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / "dlp_pass_writers"


@dataclass
class AppConfig:
    """应用全局配置。"""

    encryption_marker: str = "%TSD-Header-###%"
    # 标识串型密文容器规则（str 简写或 {name, token, window} 对象）；
    # 为空时回退 encryption_marker，行为与旧版一致。见 core/schemes/markers.py
    cipher_markers: tuple = ()
    powershell_timeout: float = 10.0
    writer_cache_dir: Path = field(default_factory=_default_writer_cache_dir)
    python_executable: str | None = None
    default_output_dir: Path = field(default_factory=_default_output_dir)
    mappings_dir: Path | None = None
    check_updates: bool = True

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        """加载配置；若给定 JSON 文件存在则覆盖默认值，否则返回默认配置。"""
        config = cls()
        if path is None or not Path(path).is_file():
            return config
        try:
            # utf-8-sig：兼容记事本 / PowerShell 写入的带 BOM 文件
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return config
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        config = cls()
        if "encryption_marker" in data:
            config.encryption_marker = str(data["encryption_marker"])
        if "cipher_markers" in data:
            raw = data["cipher_markers"] or ()
            if isinstance(raw, (list, tuple)):
                config.cipher_markers = tuple(raw)
            elif isinstance(raw, str):
                config.cipher_markers = (raw,)
        if "powershell_timeout" in data:
            config.powershell_timeout = float(data["powershell_timeout"])
        if "writer_cache_dir" in data:
            config.writer_cache_dir = Path(data["writer_cache_dir"])
        if "python_executable" in data:
            config.python_executable = str(data["python_executable"])
        if "default_output_dir" in data:
            config.default_output_dir = Path(data["default_output_dir"])
        if "mappings_dir" in data and data["mappings_dir"]:
            config.mappings_dir = Path(str(data["mappings_dir"]))
        if "check_updates" in data:
            config.check_updates = bool(data["check_updates"])
        return config

    def marker_rules(self) -> list:
        """标识串规则列表（原始 JSON 条目，交由 MarkerRule 归一化）。"""
        return list(self.cipher_markers) or [self.encryption_marker]


def load_runtime_config(project_root: Path | None = None) -> AppConfig:
    """加载运行时配置：打包产物优先 exe 同目录的 config.json，其次项目根目录。"""
    from .core.bundle import is_bundled  # 延迟导入，避免核心模块循环依赖

    candidates: list[Path] = []
    if is_bundled():
        candidates.append(Path(sys.executable).resolve().parent / "config.json")
    if project_root is not None:
        candidates.append(Path(project_root) / "config.json")
    for path in candidates:
        if path.is_file():
            return AppConfig.load(path)
    return AppConfig()
