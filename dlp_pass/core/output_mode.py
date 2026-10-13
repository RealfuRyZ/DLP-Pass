"""解密输出模式：「原地解密覆盖」与「输出到指定目录」。

界面只负责收集用户选择，任务编排与输出目录校验都在本模块完成，
因此命令行与单元测试可以复用同一套规则（代码保持 Python 3.8 兼容）。

模式语义
--------
- ``OutputMode.DIRECTORY``：解密结果写入用户选择的输出目录，保留原有相对目录结构，
  原始加密文件不被改动；
- ``OutputMode.IN_PLACE``：解密结果直接覆盖原始文件路径（核心服务会先校验新内容为
  明文再替换，失败时原文件保持不变，见 :mod:`dlp_pass.core.decrypt_service`）。
"""

from __future__ import annotations

import os
import re
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional, Tuple

from .models import DecryptTask, make_item_id

# Windows 路径中的非法字符（盘符冒号在切片校验之后不再出现）
_INVALID_PATH_CHARS_RE = re.compile(r'[<>"|?*\r\n\t]')
# 不能以点或空格结尾（Windows 会静默剥离，导致实际落盘位置与用户预期不一致）
_INVALID_PATH_ENDING_RE = re.compile(r"[. ]$")

_IN_PLACE_LABEL = "原地解密覆盖"
_DIRECTORY_LABEL = "输出到指定目录"


class OutputMode(str, Enum):
    """批处理的解密输出方式。"""

    DIRECTORY = "directory"
    IN_PLACE = "in_place"

    @property
    def label(self) -> str:
        """界面 / 日志使用的中文名称。"""
        return _IN_PLACE_LABEL if self is OutputMode.IN_PLACE else _DIRECTORY_LABEL

    @classmethod
    def from_value(cls, value: "str | OutputMode | None") -> "OutputMode":
        """容错解析（配置 / 命令行传入字符串时使用），无法识别时回退目录模式。"""
        if isinstance(value, cls):
            return value
        text = str(value or "").strip().lower()
        for member in cls:
            if member.value == text:
                return member
        return cls.DIRECTORY


def validate_output_dir(
    path_text: "str | Path", must_exist: bool = False
) -> Tuple[bool, str, Optional[Path]]:
    """校验「输出到指定目录」模式下用户填写的输出目录。

    :param must_exist: ``True`` 时要求目录已存在；默认允许尚不存在的目录
        （解密流程会自动逐级创建）。
    :return: ``(是否有效, 提示文案, 规范化后的目录路径)``；
        无效时第三个元素为 ``None``，有效时提示文案为空串。
    """
    text = str(path_text or "").strip().strip('"').strip()
    if not text:
        return False, "请先选择解密输出目录。", None

    cleaned = _INVALID_PATH_CHARS_RE.search(text)
    if cleaned is not None:
        return False, f"输出目录路径含有非法字符 {cleaned.group()!r}，请重新选择：{text}", None
    if _INVALID_PATH_ENDING_RE.search(text):
        return False, f"输出目录路径不能以点或空格结尾，请重新选择：{text}", None

    path = Path(os.path.expandvars(os.path.expanduser(text)))
    if not path.is_absolute():
        return False, f"输出目录必须是绝对路径，请重新选择：{text}", None
    try:
        resolved = Path(os.path.abspath(str(path)))
    except (OSError, ValueError) as exc:
        return False, f"输出目录路径无法解析：{text}（{exc}）", None

    if not resolved.is_absolute():  # 兜底：极少数平台性异常仍拒绝
        return False, f"输出目录必须是绝对路径，请重新选择：{text}", None

    if resolved.exists():
        if not resolved.is_dir():
            return False, f"输出目录已存在但不是文件夹：{resolved}", None
        return True, "", resolved
    if must_exist:
        return (
            False,
            f"输出目录不存在，请先创建或在界面中选择已有文件夹：{resolved}",
            None,
        )
    return True, "", resolved


def plan_decrypt_tasks(
    entries: Iterable[object],
    mode: OutputMode,
    output_root: "Optional[Path]" = None,
) -> Tuple[list, list]:
    """按输出模式把待处理条目编排成解密任务列表。

    :param entries: 具备 ``source`` / ``relative_output`` / ``suffix`` 属性的条目
        （界面的 ``FileEntry`` 或核心的 ``CollectedEntry``），``item_id`` 可选。
    :return: ``(任务列表, 冲突条目列表)``；冲突条目为 ``(item_id, 说明)``，
        调用方应把它们标记为「已跳过」。原地模式下源文件路径天然唯一，不会发生冲突。
    """
    tasks: list = []
    conflicts: list = []
    used_outputs: set = set()

    for entry in entries:
        source = Path(getattr(entry, "source"))
        if mode is OutputMode.IN_PLACE:
            output = source
        else:
            if output_root is None:
                raise ValueError("输出到指定目录模式必须提供 output_root")
            output = Path(output_root) / Path(getattr(entry, "relative_output"))

        key = make_item_id(output)
        if key in used_outputs:
            item_id = getattr(entry, "item_id", None) or make_item_id(source)
            conflicts.append((item_id, "输出路径与其它文件冲突，已跳过"))
            continue
        used_outputs.add(key)
        tasks.append(
            DecryptTask(
                source=source,
                output=output,
                suffix=str(getattr(entry, "suffix")),
                in_place=mode is OutputMode.IN_PLACE,
            )
        )
    return tasks, conflicts
