"""业务数据模型。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class TaskStatus(Enum):
    """单个文件的处理状态。"""

    PENDING = "pending"
    DECRYPTING = "decrypting"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class DecryptTask:
    """一次解密任务：源文件 -> 输出文件。"""

    source: Path
    output: Path
    suffix: str

    @property
    def item_id(self) -> str:
        return make_item_id(self.source)


@dataclass
class DecryptOutcome:
    """单次解密任务的结果。"""

    source: Path
    success: bool
    output: Path | None = None
    cancelled: bool = False
    process_name: str | None = None
    sha256: str | None = None
    error: str | None = None
    attempts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CollectedEntry:
    """收集阶段发现的候选文件。"""

    source: Path
    relative_output: Path
    suffix: str


def make_item_id(path: Path | str) -> str:
    """生成与平台大小写规则一致的文件唯一标识。"""
    return os.path.normcase(str(Path(path)))
