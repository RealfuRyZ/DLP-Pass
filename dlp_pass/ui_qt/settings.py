"""用户界面偏好的本地持久化（主题等）。

偏好文件默认位于 ``%LOCALAPPDATA%\\DLP-Pass\\settings.json``（与日志回退目录
同源，保证安装版在程序目录不可写时也能保存）。测试或需要重定向位置时，可设置
环境变量 ``DLP_PASS_SETTINGS_DIR`` 指定所在目录。读写失败一律静默降级为默认值，
绝不阻塞程序启动。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

_logger = logging.getLogger(__name__)

SETTINGS_FILENAME = "settings.json"
THEME_KEY = "theme"
VALID_THEMES = ("dark", "light")


def settings_path() -> Path:
    """当前生效的偏好文件路径（每次调用现取环境变量，便于测试重定向）。"""
    override = os.environ.get("DLP_PASS_SETTINGS_DIR", "").strip()
    if override:
        base = Path(override)
    else:
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DLP-Pass"
    return base / SETTINGS_FILENAME


def _read_all() -> dict:
    path = settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:  # ValueError 覆盖 json.JSONDecodeError
        _logger.debug("界面偏好文件不可用，按默认值处理: %s (%s)", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def load_theme(default: str = "dark") -> str:
    """读取已保存的主题偏好；缺失 / 非法 / 读取失败时返回 ``default``。"""
    value = str(_read_all().get(THEME_KEY, default)).strip().lower()
    return value if value in VALID_THEMES else default


def save_theme(name: str) -> bool:
    """保存主题偏好（合并写入既有键）；成功返回 ``True``，失败告警并返回 ``False``。"""
    theme_name = name if name in VALID_THEMES else "dark"
    data = _read_all()
    data[THEME_KEY] = theme_name
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError as exc:
        _logger.warning("界面偏好保存失败: %s (%s)", path, exc)
        return False
