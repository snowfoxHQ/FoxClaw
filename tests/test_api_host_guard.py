"""API 层 Host 头校验(DNS rebinding 防护)。

攻击模型:本机 `foxclaw serve`(回环监听、无 API key)时,用户被诱导打开 evil.com;
evil.com 解析到 127.0.0.1,页面内 fetch 打本机 /api/*,同源成立 → 可读写本地数据面。
防线是校验 Host 头:浏览器发请求时 Host 必须与被访问域名一致,DNS 改解析也绕不过。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from foxclaw.api.middleware import _api_key_guard, _host_header_allowed


@pytest.mark.parametrize("host", [
    "127.0.0.1:8000", "localhost:8000", "localhost", "TESTCLIENT",
    "[::1]:8000", "192.168.1.9:8000", "10.0.0.2", "mypc", "",
])
def test_allows_local_and_lan_hosts(host: str) -> None:
    assert _host_header_allowed(host) is True


@pytest.mark.parametrize("host", [
    "evil.com", "attacker.example.org:8000", "sub.evil.com:80",
])
def test_rejects_public_domain_hosts(host: str) -> None:
    assert _host_header_allowed(host) is False


def test_allowlist_escape_hatch(monkeypatch) -> None:
    monkeypatch.setenv("FOXCLAW_ALLOWED_HOSTS", "my.host.net, *.corp.example")
    assert _host_header_allowed("my.host.net") is True
    assert _host_header_allowed("a.corp.example") is True
    assert _host_header_allowed("evil.com") is False


def _request(path: str, host: str, client: str = "127.0.0.1"):
    return SimpleNamespace(
        url=SimpleNamespace(path=path),
        client=SimpleNamespace(host=client),
        headers={"host": host, "X-API-Key": ""},
        method="GET",
    )


async def _ok(_request):
    return SimpleNamespace(status_code=200)


@pytest.mark.parametrize("path", ["/api/task", "/knowledge/ingest", "/config/llm"])
def test_loopback_with_public_host_is_forbidden(path: str, monkeypatch) -> None:
    monkeypatch.delenv("FOXCLAW_API_KEY", raising=False)
    resp = asyncio.run(_api_key_guard(_request(path, "evil.com"), _ok))
    assert resp.status_code == 403, "本机客户端 + 公网域名 Host 必须拒绝"


def test_loopback_with_local_host_passes(monkeypatch) -> None:
    monkeypatch.delenv("FOXCLAW_API_KEY", raising=False)
    resp = asyncio.run(_api_key_guard(_request("/api/task", "127.0.0.1:8000"), _ok))
    assert resp.status_code == 200


def test_probe_paths_not_affected(monkeypatch) -> None:
    monkeypatch.delenv("FOXCLAW_API_KEY", raising=False)
    resp = asyncio.run(_api_key_guard(_request("/health", "evil.com"), _ok))
    assert resp.status_code == 200, "探针不在数据面防护范围"
