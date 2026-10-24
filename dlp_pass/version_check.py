"""GitHub Release 版本检测。

只依赖标准库，便于单元测试；网络失败、超时、HTTP 错误或响应格式异常时
统一返回 ``None``，界面据此静默降级为「仅显示当前版本」。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request

import dlp_pass

_logger = logging.getLogger(__name__)

GITHUB_REPO = "RealfuRyZ/DLP-Pass"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASE_PAGE_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"
REQUEST_TIMEOUT = 4.0

_VERSION_RE = re.compile(r"(\d+(?:\.\d+){0,3})")


def parse_version(value: str | None) -> tuple[int, ...] | None:
    """从 ``v2.4.0`` / ``2.4.0`` 等字符串提取数字版本元组；无法解析返回 ``None``。"""
    if not value:
        return None
    match = _VERSION_RE.search(str(value).strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest: str | None, current: str | None) -> bool:
    """判断 ``latest`` 是否比 ``current`` 新（无法解析时返回 ``False``）。"""
    latest_parts = parse_version(latest)
    current_parts = parse_version(current)
    if latest_parts is None or current_parts is None:
        return False
    width = max(len(latest_parts), len(current_parts), 4)
    latest_padded = latest_parts + (0,) * (width - len(latest_parts))
    current_padded = current_parts + (0,) * (width - len(current_parts))
    return latest_padded > current_padded


def fetch_latest_version(
    timeout: float = REQUEST_TIMEOUT,
    opener=None,
) -> str | None:
    """请求 GitHub 最新 Release 并返回 tag（如 ``v2.4.0``）；失败返回 ``None``。

    ``opener`` 仅用于测试注入，签名需兼容 ``urllib.request.urlopen``。
    """
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "User-Agent": f"DLP-Pass/{dlp_pass.__version__}",
            "Accept": "application/vnd.github+json",
        },
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                _logger.debug("版本检查返回 HTTP %s", status)
                return None
            payload = response.read()
        data = json.loads(payload.decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        _logger.debug("版本检查失败：%s", exc)
        return None

    if not isinstance(data, dict):
        return None
    tag = data.get("tag_name")
    if not isinstance(tag, str) or parse_version(tag) is None:
        return None
    return tag
