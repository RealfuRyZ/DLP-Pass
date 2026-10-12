"""应用配置。

集中管理密文标识、超时、缓存目录、默认输出目录与映射目录覆盖等参数。
可通过 ``config.json`` 覆盖默认值（查找顺序：exe 同目录 → 项目根目录），示例：

.. code-block:: json

    {
        "powershell_timeout": 10.0,
        "default_output_dir": "D:/解密输出",
        "mappings_dir": "D:/my-mappings"
    }

本程序完全离线运行：不连接任何数据库或网络服务，映射规则由本地
``mappings/`` 目录提供（见 :mod:`dlp_pass.core.mapping_loader`）。
"""

from __future__ import annotations

import json
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
    powershell_timeout: float = 10.0
    writer_cache_dir: Path = field(default_factory=_default_writer_cache_dir)
    python_executable: str | None = None
    default_output_dir: Path = field(default_factory=_default_output_dir)
    mappings_dir: Path | None = None

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
        return config
