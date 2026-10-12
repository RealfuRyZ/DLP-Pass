"""文件收集：将拖入的文件 / 文件夹展开为待处理文件列表。

- 文件夹递归展开，并在输出目录中保留相对目录结构；
- 自动剪枝常见「非资料」目录（.venv / __pycache__ / .git 等），
  避免把虚拟环境、缓存等成千上万的无关文件纳入扫描；
- 支持数量上限（防止误拖系统盘导致超长任务）；
- 对相同源文件去重（同一文件被同时拖入多次时只处理一次）。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from pathlib import Path

from .models import CollectedEntry, make_item_id

# 递归时跳过的目录名（小写比较）：虚拟环境 / 缓存 / 版本库等
_BLOCKED_DIR_NAMES = frozenset({
    ".git", ".svn", ".hg", ".venv", "venv", "env",
    "__pycache__", "node_modules", ".idea", ".vscode",
    ".mypy_cache", ".pytest_cache", ".tox",
})


def iter_files(root: Path) -> Iterator[Path]:
    """递归遍历目录下的文件（剪枝无关目录，顺序稳定）。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d.lower() not in _BLOCKED_DIR_NAMES)
        for name in sorted(filenames):
            yield Path(dirpath) / name


def collect_entries(
    dropped_paths: Iterable[str | Path],
    limit: int | None = None,
) -> Iterator[CollectedEntry]:
    """展开所有拖入路径，逐项产出候选文件（保持拖入顺序）。

    :param limit: 最多产出的文件数量（``None`` 表示不限制）。
    """
    seen: set[str] = set()
    count = 0
    for raw in dropped_paths:
        path = Path(raw)
        if path.is_dir():
            base = path.parent
            for file_path in iter_files(path):
                if limit is not None and count >= limit:
                    return
                entry = _make_entry(file_path, file_path.relative_to(base), seen)
                if entry is not None:
                    count += 1
                    yield entry
        elif path.is_file():
            if limit is not None and count >= limit:
                return
            entry = _make_entry(path, Path(path.name), seen)
            if entry is not None:
                count += 1
                yield entry


def _make_entry(source: Path, relative_output: Path, seen: set[str]) -> CollectedEntry | None:
    key = make_item_id(source)
    if key in seen:
        return None
    seen.add(key)
    return CollectedEntry(
        source=source,
        relative_output=relative_output,
        suffix=source.suffix.lower(),
    )
