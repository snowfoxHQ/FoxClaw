"""浏览器请求级准入(跳转 / iframe / 子资源)与路由拦截。

只测判定与回调,不起真浏览器:PlaywrightDriver 的模块顶层不 import playwright,
用 `object.__new__` 绕过 __init__ 即可直接测 `_on_route`。
"""

import pytest

from foxclaw.browser.action import url_policy
from foxclaw.browser.driver.playwright_driver import PlaywrightDriver


# ── 判定层 ────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "data:image/png;base64,iVBORw0KGgo=",
    "blob:https://example.com/2f8c-4a",
    "about:blank",
])
def test_internal_schemes_are_allowed(monkeypatch, url):
    """data:/blob:/about: 由页面自身产生、不过网络栈,拦了只会让内联图片裂掉。"""
    monkeypatch.delenv("FOXCLAW_BROWSER_ALLOW_PRIVATE", raising=False)
    assert url_policy.validate_request_url(url) == url


@pytest.mark.parametrize("url", [
    "file:///C:/Windows/win.ini",
    "javascript:alert(1)",
    "ftp://example.com/x",
    "chrome-extension://abcdef/page.html",
])
def test_non_http_schemes_are_rejected(monkeypatch, url):
    monkeypatch.delenv("FOXCLAW_BROWSER_ALLOW_PRIVATE", raising=False)
    with pytest.raises(ValueError):
        url_policy.validate_request_url(url)


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:8080/admin",
    "http://[::1]/",
    "http://10.0.0.5/",
    "http://192.168.1.1/",
])
def test_private_addresses_are_rejected(monkeypatch, url):
    monkeypatch.delenv("FOXCLAW_BROWSER_ALLOW_PRIVATE", raising=False)
    with pytest.raises(ValueError):
        url_policy.validate_request_url(url)


def test_allow_private_env_opens_the_gate(monkeypatch):
    monkeypatch.setenv("FOXCLAW_BROWSER_ALLOW_PRIVATE", "1")
    assert url_policy.validate_request_url("http://127.0.0.1:8080/") == "http://127.0.0.1:8080/"


def test_public_ip_literal_passes_without_dns(monkeypatch):
    """IP 字面量不该触发解析:断网环境下也得能判定。"""
    monkeypatch.delenv("FOXCLAW_BROWSER_ALLOW_PRIVATE", raising=False)

    def _boom(*_a, **_k):
        raise AssertionError("IP 字面量不应该走 getaddrinfo")

    monkeypatch.setattr(url_policy.socket, "getaddrinfo", _boom)
    assert url_policy.validate_request_url("http://93.184.216.34/x") == "http://93.184.216.34/x"


def test_domain_resolving_to_internal_is_rejected(monkeypatch):
    """域名指向内网必须拒。

    回归点:旧实现把 `raise ValueError` 写在自己那个 `try/except ValueError: continue`
    里,拒绝分支被同一个 except 吞掉 —— 「域名解析到内网」从来没生效过。
    """
    monkeypatch.delenv("FOXCLAW_BROWSER_ALLOW_PRIVATE", raising=False)
    monkeypatch.setattr(url_policy, "_resolve_internal", lambda host: True)
    with pytest.raises(ValueError) as ei:
        url_policy.validate_request_url("http://intranet.example.com/x")
    assert "内网" in str(ei.value)


def test_domain_resolving_to_public_passes(monkeypatch):
    monkeypatch.delenv("FOXCLAW_BROWSER_ALLOW_PRIVATE", raising=False)
    monkeypatch.setattr(url_policy, "_resolve_internal", lambda host: False)
    assert url_policy.validate_request_url(
        "https://example.com/a.png") == "https://example.com/a.png"


def test_resolution_is_cached_between_requests(monkeypatch):
    """几十个子资源同域:只在首个请求上解析,后续走缓存。"""
    monkeypatch.delenv("FOXCLAW_BROWSER_ALLOW_PRIVATE", raising=False)
    url_policy._resolve_cache.clear()
    calls = []

    def _fake_getaddrinfo(host, *_a, **_k):
        calls.append(host)
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(url_policy.socket, "getaddrinfo", _fake_getaddrinfo)
    for _ in range(5):
        url_policy.validate_request_url("http://cdn.example.com/a.js")
    assert calls == ["cdn.example.com"]
    url_policy._resolve_cache.clear()


def test_short_ipv6_with_scope_is_not_mistaken(monkeypatch):
    """解析结果可能带 scope id(fe80::1%eth0),不能因此算「非内网」。"""
    monkeypatch.delenv("FOXCLAW_BROWSER_ALLOW_PRIVATE", raising=False)
    url_policy._resolve_cache.clear()
    monkeypatch.setattr(url_policy.socket, "getaddrinfo",
                        lambda host, *a, **k: [(10, 1, 6, "", ("fe80::1%eth0", 0, 0, 1))])
    with pytest.raises(ValueError):
        url_policy.validate_request_url("http://v6.example.com/")
    url_policy._resolve_cache.clear()


# ── 路由回调 ──────────────────────────────────────────────────

class _FakeRoute:
    def __init__(self, url):
        self.request = type("Req", (), {"url": url})()
        self.aborted = None
        self.continued = False

    def abort(self, reason=None):
        self.aborted = reason or "abort"

    def continue_(self):
        self.continued = True


def _driver(monkeypatch):
    monkeypatch.delenv("FOXCLAW_BROWSER_ALLOW_PRIVATE", raising=False)
    d = object.__new__(PlaywrightDriver)
    d.blocked_requests = []
    return d


def test_route_aborts_internal_subresource(monkeypatch):
    """子资源打云元数据:这是「只校验 navigate 目标」漏掉的典型。"""
    d = _driver(monkeypatch)
    r = _FakeRoute("http://169.254.169.254/latest/meta-data/iam/")
    d._on_route(r)
    assert r.aborted == "blockedbyclient"
    assert r.continued is False
    assert d.blocked_requests and "169.254" in d.blocked_requests[0][0]


def test_route_continues_public_request(monkeypatch):
    d = _driver(monkeypatch)
    r = _FakeRoute("http://93.184.216.34/index.html")
    d._on_route(r)
    assert r.continued is True and r.aborted is None


def test_route_continues_data_uri(monkeypatch):
    d = _driver(monkeypatch)
    r = _FakeRoute("data:image/gif;base64,R0lGOD")
    d._on_route(r)
    assert r.continued is True and r.aborted is None


def test_route_aborts_redirect_target(monkeypatch):
    """3xx 跳转的目标是独立请求,同样过这一层(否则公网页跳一次转就绕过了)。"""
    d = _driver(monkeypatch)
    r = _FakeRoute("http://127.0.0.1:9200/_cat/indices")
    d._on_route(r)
    assert r.aborted == "blockedbyclient"


def test_blocked_log_is_bounded(monkeypatch):
    d = _driver(monkeypatch)
    for i in range(70):
        d._on_route(_FakeRoute(f"http://127.0.0.1/{i}"))
    assert len(d.blocked_requests) <= 50


def test_route_survives_closed_session(monkeypatch):
    """回调和 close 竞争时 continue_ 会抛,不能让它冒到 Playwright 的事件循环。"""
    d = _driver(monkeypatch)

    class _DeadRoute(_FakeRoute):
        def continue_(self):
            raise RuntimeError("Target closed")

    d._on_route(_DeadRoute("http://93.184.216.34/x"))
