"""版本号解析与 GitHub Release 检测测试（全部离线，不访问网络）。"""

from __future__ import annotations

import json
import unittest
import urllib.error

from dlp_pass.config import AppConfig
from dlp_pass.version_check import fetch_latest_version, is_newer, parse_version


class _Response:
    def __init__(self, payload, status: int = 200):
        self.status = status
        self._payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class VersionParseTest(unittest.TestCase):
    def test_parse_common_tags(self) -> None:
        self.assertEqual(parse_version("v2.3.2"), (2, 3, 2))
        self.assertEqual(parse_version("2.4"), (2, 4))
        self.assertEqual(parse_version("release-2.4.0"), (2, 4, 0))
        self.assertIsNone(parse_version("latest"))
        self.assertIsNone(parse_version(None))

    def test_is_newer(self) -> None:
        self.assertTrue(is_newer("v2.4.0", "2.3.2"))
        self.assertTrue(is_newer("2.3.3", "2.3.2"))
        self.assertFalse(is_newer("v2.3.2", "2.3.2"))
        self.assertFalse(is_newer("v2.3.1", "2.3.2"))
        self.assertFalse(is_newer("bad", "2.3.2"))
        self.assertFalse(is_newer("v2.4.0", "bad"))


class FetchLatestVersionTest(unittest.TestCase):
    def test_success_and_request_headers(self) -> None:
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["timeout"] = timeout
            return _Response({"tag_name": "v2.4.0"})

        latest = fetch_latest_version(timeout=2.5, opener=opener)
        self.assertEqual(latest, "v2.4.0")
        self.assertIn("/releases/latest", captured["url"])
        self.assertIn("DLP-Pass/", captured["headers"]["User-agent"])
        self.assertEqual(captured["timeout"], 2.5)

    def test_missing_or_invalid_tag(self) -> None:
        self.assertIsNone(fetch_latest_version(opener=lambda *a, **k: _Response({})))
        self.assertIsNone(
            fetch_latest_version(opener=lambda *a, **k: _Response({"tag_name": "latest"}))
        )

    def test_network_error(self) -> None:
        def opener(*args, **kwargs):
            raise urllib.error.URLError("offline")

        self.assertIsNone(fetch_latest_version(opener=opener))

    def test_invalid_json(self) -> None:
        self.assertIsNone(fetch_latest_version(opener=lambda *a, **k: _Response(b"not json")))

    def test_non_200_status(self) -> None:
        response = _Response({"tag_name": "v2.4.0"}, status=403)
        self.assertIsNone(fetch_latest_version(opener=lambda *a, **k: response))


class UpdateConfigTest(unittest.TestCase):
    def test_check_updates_default_and_override(self) -> None:
        self.assertTrue(AppConfig().check_updates)
        self.assertFalse(AppConfig._from_dict({"check_updates": False}).check_updates)


if __name__ == "__main__":
    unittest.main()
