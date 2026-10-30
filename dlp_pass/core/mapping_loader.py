"""文件版映射加载器：从 ``mappings/`` 目录读取「文件后缀 -> 伪装进程」映射。

约定（详见 ``mappings/README.md``）
----------------------------------
- 目录内每个 JSON 文件即一个映射版本，文件名（去扩展名）为版本号，
  例如 ``20261009.1.json``；以 ``_`` 或 ``.`` 开头的文件视为草稿，不参与生效；
- **程序启动时始终读取版本号最大的文件作为当前生效映射**
  （版本号按 ``.`` / ``_`` / ``-`` 分段，数字段按数值比较）；
- 映射目录查找优先级：配置 ``mappings_dir`` -> exe 同目录 ``mappings/``
  （用户自定义覆盖）-> 随包内置 ``mappings/``（发布版）-> 项目根 ``mappings/``（源码运行）；
- 目录缺失、无有效文件或后缀未配置时，回退 :mod:`dlp_pass.config` 内置兜底规则。

本模块是早期 MariaDB 映射库的本地文件替代：程序从此完全离线、开源可审计。
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..config import DEFAULT_FALLBACK_RULE, DEFAULT_PDF_RULE, AppConfig, ProcessRule
from ..debug_mode import head_preview, is_debug_enabled, save_bytes
from .bundle import find_data_dir, is_bundled

_logger = logging.getLogger(__name__)

_VERSION_SPLIT_RE = re.compile(r"[._\-]+")


def version_key(version: str) -> tuple:
    """把版本号（如 ``20261009.1``）转为可比较排序键。

    数字段按数值比较、非数字段按小写字符串比较，混合形态不会出现类型错误::

        version_key("20261009.1") < version_key("20261012.1")   # True
    """
    parts: list[tuple[int, int, str]] = []
    for piece in _VERSION_SPLIT_RE.split(version):
        if piece.isdigit():
            parts.append((0, int(piece), ""))
        elif piece:
            parts.append((1, 0, piece.lower()))
    return tuple(parts)


@dataclass
class MappingSet:
    """一次加载得到的映射版本（含后缀 -> 规则查询）。"""

    version: str
    rules: dict[str, ProcessRule] = field(default_factory=dict)
    source: Path | None = None
    is_fallback: bool = False

    def get_process_queue(self, file_suffix: str) -> list[str]:
        """返回按尝试顺序排列的伪装进程队列：``[主进程, 备用...]``。

        后缀未配置时回退内置兜底规则（与原数据库版行为一致）。
        """
        suffix = file_suffix.lower()
        rule = self.rules.get(suffix)
        if rule is None:
            rule = DEFAULT_PDF_RULE if suffix == ".pdf" else DEFAULT_FALLBACK_RULE
            _logger.warning("映射中未配置后缀 %s，使用兜底规则: %s", suffix, rule.as_queue())
            _logger.debug("映射查询(兜底): %s -> %s", suffix, rule.as_queue())
            return rule.as_queue()
        queue = rule.as_queue()
        _logger.debug("映射查询: %s -> %s（版本 %s）", suffix, queue, self.version)
        return queue

    def describe(self) -> str:
        """人类可读的来源描述（日志/界面用）。"""
        if self.is_fallback:
            return "内置兜底规则（未找到映射文件）"
        return f"{self.version}（{len(self.rules)} 条规则，来源 {self.source}）"


def _parse_rule(entry: Any) -> tuple[str, int, ProcessRule] | None:
    """解析单条规则；缺字段/被禁用/类型错误返回 ``None``（调用方跳过）。"""
    if not isinstance(entry, dict):
        return None
    extension = str(entry.get("extension", "")).strip().lower()
    primary = str(entry.get("primary", "")).strip()
    if not extension or not primary or entry.get("enabled", True) is False:
        return None
    alternatives_raw = entry.get("alternatives", []) or []
    if isinstance(alternatives_raw, str):  # 兼容逗号分隔写法
        alternatives_raw = [p.strip() for p in alternatives_raw.split(",") if p.strip()]
    alternatives = tuple(str(a).strip() for a in alternatives_raw if str(a).strip())
    try:
        priority = int(entry.get("priority", 0))
    except (TypeError, ValueError):
        priority = 0
    return extension, priority, ProcessRule(primary, alternatives)


def load_mapping_file(path: Path) -> MappingSet | None:
    """解析单个映射版本文件；文件不可用或格式不符返回 ``None`` 并告警。

    以 ``utf-8-sig`` 读取：兼容 Windows 记事本 / PowerShell 写入的带 BOM 文件。
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _logger.warning("映射文件无法读取，已跳过: %s (%s)", path, exc)
        return None
    _debug_capture_raw(path, raw, parsed=_is_json_mapping(raw))
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _logger.warning("映射文件无法解析，已跳过: %s (%s)", path, exc)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        _logger.warning("映射文件格式不符预期（缺少 rules 列表），已跳过: %s", path)
        return None

    file_version = path.stem
    inner_version = str(data.get("version", "")).strip()
    if inner_version and inner_version != file_version:
        _logger.warning(
            "映射文件 %s 内部 version=%s 与文件名不一致，以文件名（排序依据）为准",
            path.name,
            inner_version,
        )

    best: dict[str, tuple[int, ProcessRule]] = {}
    for item in data["rules"]:
        parsed = _parse_rule(item)
        if parsed is None:
            continue
        extension, priority, rule = parsed
        current = best.get(extension)
        if current is None or priority > current[0]:
            best[extension] = (priority, rule)
    rules = {extension: rule for extension, (_, rule) in best.items()}
    return MappingSet(file_version, rules, source=path)


def find_mapping_dir(
    config: AppConfig | None = None,
    project_root: Path | None = None,
) -> Path | None:
    """按优先级定位映射目录：显式配置 -> exe 同目录 -> 随包内置 -> 项目根。"""
    if config is not None and config.mappings_dir is not None:
        return Path(config.mappings_dir)
    candidates: list[Path] = []
    if is_bundled():
        candidates.append(Path(sys.executable).resolve().parent / "mappings")
        bundled = find_data_dir("mappings")
        if bundled is not None:
            candidates.append(bundled)
    if project_root is not None:
        candidates.append(Path(project_root) / "mappings")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def iter_version_files(directory: Path) -> Iterable[Path]:
    """目录内的候选版本文件（``_`` / ``.`` 前缀视为草稿跳过）。"""
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith(("_", ".")):
            continue
        yield path


def load_latest(
    config: AppConfig | None = None,
    project_root: Path | None = None,
) -> MappingSet:
    """加载当前生效映射：始终取映射目录中版本号最大的有效文件。

    找不到目录/文件时返回内置兜底规则（``is_fallback=True``），绝不抛异常。
    """
    directory = find_mapping_dir(config, project_root)
    if directory is not None:
        candidates = list(iter_version_files(directory))
        _logger.debug("映射目录: %s（候选文件 %s）", directory, [p.name for p in candidates])
        versions = [m for m in (load_mapping_file(p) for p in candidates) if m is not None]
        if versions:
            active = max(versions, key=lambda m: version_key(m.version))
            _logger.info("已加载映射规则: %s", active.describe())
            _logger.debug("映射规则明细: %s", _rules_summary(active))
            return active
        _logger.warning("映射目录 %s 中没有可用版本文件，使用内置兜底规则", directory)
    else:
        _logger.warning("未找到 mappings/ 映射目录，使用内置兜底规则")
    return MappingSet("内置兜底", is_fallback=True)


def debug_snapshot(mapping: MappingSet) -> None:
    """调试模式：以当前进程原始视角复读当前生效映射文件并留档。

    用于验证「映射规则文件本身是否被 DLP 驱动加密」：若留档副本头部不是
    JSON 明文（或无法解析），说明本进程读到的是磁盘密文，映射内容不可信。
    """
    if not is_debug_enabled():
        return
    if mapping.is_fallback or mapping.source is None:
        _logger.debug("映射调试快照: 当前为内置兜底规则，无映射文件可核对")
        return
    try:
        raw = mapping.source.read_bytes()
    except OSError as exc:
        _logger.debug("映射调试快照: 读取映射文件失败 %s: %s", mapping.source, exc)
        return
    _debug_capture_raw(mapping.source, raw, parsed=_is_json_mapping(raw), prefix="mapping-active-")


def _is_json_mapping(raw: bytes) -> bool:
    """原始字节能否解析为「含 rules 列表」的映射 JSON（判断是否被转加密）。"""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and isinstance(data.get("rules"), list)


def _debug_capture_raw(path: Path, raw: bytes, parsed: bool, prefix: str = "mapping-") -> None:
    """调试模式下记录映射文件原始字节概况并复制留档（非调试模式零开销）。"""
    if not is_debug_enabled():
        return
    _logger.debug(
        "映射文件原始视角: %s 大小 %d 字节 头部 %s JSON解析=%s",
        path.name, len(raw), head_preview(raw), "成功" if parsed else "失败（可能被 DLP 加密！）",
    )
    saved = save_bytes(prefix + path.name + ".raw.bin", raw)
    if saved is not None:
        _logger.debug("映射文件原始副本已留档: %s", saved)


def _rules_summary(mapping: MappingSet) -> str:
    """规则字典的简短摘要（调试日志用）：后缀 -> 主进程[+备用数]。"""
    return ", ".join(
        f"{suffix}->{rule.primary}(+{len(rule.alternatives)})"
        for suffix, rule in sorted(mapping.rules.items())
    )
