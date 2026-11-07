"""浏览器导航 URL 准入策略(SSRF / 本地文件读取防护)。

背景:`ActionExecutor._do_navigate` 原本把模型给的字符串直接交给 driver,
`file://` 可读本机文件、`javascript:` 可在页面上下文执行、内网地址可作探测跳板。
"""

from __future__ import annotations

import pytest

from foxclaw.browser.action.url_policy import validate_navigate_url


@pytest.mark.parametrize("url", [
    "file:///C:/Windows/win.ini",
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "blob:https://example.com/uuid",
    "ftp://example.com/x",
])
def test_rejects_non_http_schemes(url: str) -> None:
    with pytest.raises(ValueError):
        validate_navigate_url(url)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/api/ahs/run",
    "http://localhost/admin",
    "http://[::1]:9000/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/",
    "http://192.168.1.1/",
])
def test_rejects_internal_targets(url: str) -> None:
    with pytest.raises(ValueError):
        validate_navigate_url(url)


def test_allows_public_url() -> None:
    assert validate_navigate_url("https://example.com/a?b=1") == "https://example.com/a?b=1"


def test_allow_private_escape_hatch(monkeypatch) -> None:
    assert validate_navigate_url("http://127.0.0.1:8000/", allow_private=True) \
        == "http://127.0.0.1:8000/"
    monkeypatch.setenv("FOXCLAW_BROWSER_ALLOW_PRIVATE", "1")
    assert validate_navigate_url("http://192.168.1.10/") == "http://192.168.1.10/"


def test_rejects_empty_and_missing_host() -> None:
    for bad in ("", "   ", "http://"):
        with pytest.raises(ValueError):
            validate_navigate_url(bad)
