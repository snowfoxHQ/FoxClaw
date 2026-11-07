"""渠道出站签名 / 入站验签。

锁住:canonical 结构不被随手改、正文与时间戳都在签名覆盖内、重放被拒、header 名大小写
无关、桥接层"签的就是发的"(content= 的字节)且签名失败 fail-closed 不发。
"""

from __future__ import annotations

import json

import httpx
import pytest

from foxclaw.channels import OutboundMessage
from foxclaw.channels import signing as S
from foxclaw.channels.openclaw_bridge import OpenClawBridgeAdapter

SECRET = "test-shared-secret"


def _headers(secret: str = SECRET, body: bytes = b'{"a":1}', ts: float = 1_700_000_000):
    signer = S.OutboundSigner(secret, key_id="k1")
    return signer.headers(body, nonce="n0nce", now=ts)


# ── canonical / 签名 ────────────────────────────────────────

def test_canonical_format_is_locked() -> None:
    """canonical 是两端的协议:字段顺序/分隔符一改,对方全部验签失败。"""
    line = S.canonical("1700000000", "abc", b"body").decode()
    assert line == f"v1\n1700000000\nabc\n{S.body_digest(b'body')}"
    assert len(line.split("\n")) == 4


def test_roundtrip_ok_and_body_tamper_detected() -> None:
    body = b'{"text":"hi"}'
    h = _headers(body=body)
    ok, why = S.verify(h, body, SECRET, now=1_700_000_000)
    assert (ok, why) == (True, "ok")
    ok, why = S.verify(h, b'{"text":"transfer 5000"}', SECRET, now=1_700_000_000)
    assert (ok, why) == (False, "bad_signature"), "正文被改必须失效"


def test_timestamp_skew_rejected() -> None:
    body = b"{}"
    h = _headers(body=body, ts=1_700_000_000)
    ok, why = S.verify(h, body, SECRET, tolerance=300, now=1_700_000_000 + 301)
    assert (ok, why) == (False, "expired")
    ok, _ = S.verify(h, body, SECRET, tolerance=300, now=1_700_000_000 + 299)
    assert ok is True


def test_signature_cannot_be_forged_with_wrong_secret() -> None:
    body = b"{}"
    h = _headers(body=body)
    ok, why = S.verify(h, body, "another-secret", now=1_700_000_000)
    assert (ok, why) == (False, "bad_signature")


def test_replay_guard_rejects_second_use() -> None:
    body = b"{}"
    h = _headers(body=body)
    guard = S.ReplayGuard(ttl=300)
    assert S.verify(h, body, SECRET, replay=guard, now=1_700_000_000) == (True, "ok")
    assert S.verify(h, body, SECRET, replay=guard, now=1_700_000_000) == (False, "replay")
    # 换一个 nonce(正常新请求)→ 通过
    h2 = S.OutboundSigner(SECRET).headers(body, nonce="other", now=1_700_000_000)
    assert S.verify(h2, body, SECRET, replay=guard, now=1_700_000_000) == (True, "ok")


def test_verify_reason_codes() -> None:
    body = b"{}"
    assert S.verify({}, body, SECRET)[1] == "missing_header"
    assert S.verify(_headers(body=body), body, "")[1] == "missing_secret"
    bad = dict(_headers(body=body))
    bad[S.SIGNATURE_HEADER] = "v2=deadbeef"
    assert S.verify(bad, body, SECRET, now=1_700_000_000)[1] == "bad_version"
    no_ts = dict(_headers(body=body))
    no_ts[S.TIMESTAMP_HEADER] = "not-a-number"
    assert S.verify(no_ts, body, SECRET)[1] == "bad_version"


def test_header_lookup_is_case_insensitive() -> None:
    body = b"{}"
    h = {k.lower(): v for k, v in _headers(body=body).items()}   # ASGI 常给小写
    assert S.verify(h, body, SECRET, now=1_700_000_000) == (True, "ok")


# ── signer ─────────────────────────────────────────────────

def test_signer_optional_key_id_and_disabled_state() -> None:
    h = S.OutboundSigner(SECRET, key_id="k9").headers(b"{}")
    assert h[S.KEY_ID_HEADER] == "k9"
    assert S.SIGNATURE_HEADER in h and h[S.TIMESTAMP_HEADER].isdigit()
    off = S.OutboundSigner("")
    assert off.enabled is False
    with pytest.raises(ValueError):
        off.headers(b"{}")


def test_from_env_opt_in() -> None:
    assert S.from_env({}) is None
    s = S.from_env({S.ENV_SECRET: "abc", S.ENV_KEY_ID: "k2"})
    assert s is not None and s.key_id == "k2" and s.enabled
    assert S.from_env({S.ENV_SECRET: "   "}) is None


# ── 桥接层 ─────────────────────────────────────────────────

class _Resp:
    status_code = 200
    text = ""

    def json(self):
        return {"message_id": "m1"}


class _Client:
    """捕获真实发出的 (url, content, headers) —— 签名必须覆盖这些字节。"""

    captured: dict = {}

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, content=None, headers=None, timeout=None):
        _Client.captured = {"url": url, "content": content, "headers": dict(headers or {})}
        return _Resp()


@pytest.fixture
def fake_http(monkeypatch):
    _Client.captured = {}
    monkeypatch.setattr(httpx, "Client", _Client)
    return _Client


def _msg() -> OutboundMessage:
    return OutboundMessage(conversation_id="c1", text="hi", extra={"upstream_channel": "wechat"})


def test_bridge_signs_the_bytes_it_sends(fake_http) -> None:
    bridge = OpenClawBridgeAdapter("http://gw.local", api_token="t",
                                   signer=S.OutboundSigner(SECRET))
    res = bridge.send(_msg())
    assert res.ok and res.message_id == "m1"
    cap = fake_http.captured
    body = cap["content"]
    assert isinstance(body, bytes) and json.loads(body)["text"] == "hi"
    assert cap["headers"]["Content-Type"] == "application/json"
    assert cap["headers"]["Authorization"] == "Bearer t"
    ok, why = S.verify(cap["headers"], body, SECRET)      # 网关侧照此验签
    assert (ok, why) == (True, "ok")


def test_bridge_unsigned_without_secret(fake_http, monkeypatch) -> None:
    monkeypatch.delenv(S.ENV_SECRET, raising=False)
    bridge = OpenClawBridgeAdapter("http://gw.local")
    assert bridge.signer is None
    assert bridge.send(_msg()).ok
    assert S.SIGNATURE_HEADER not in fake_http.captured["headers"]
    assert fake_http.captured["headers"]["Content-Type"] == "application/json"


def test_bridge_fail_closed_when_signing_fails(fake_http) -> None:
    class _Broken:
        enabled = True

        def headers(self, body, nonce="", now=None):
            raise RuntimeError("kms down")

    bridge = OpenClawBridgeAdapter("http://gw.local", signer=_Broken())
    res = bridge.send(_msg())
    assert res.ok is False and "签名失败" in res.error
    assert fake_http.captured == {}, "签名失败必须不发(fail-closed)"


def test_bridge_verify_webhook(fake_http) -> None:
    body = b'{"message":{"text":"hi"}}'
    bridge = OpenClawBridgeAdapter("http://gw.local", signer=S.OutboundSigner(SECRET))
    h = S.OutboundSigner(SECRET).headers(body)
    assert bridge.verify_webhook(h, body) == (True, "ok")
    assert bridge.verify_webhook(h, body + b" ") == (False, "bad_signature")

    unsigned = OpenClawBridgeAdapter("http://gw.local", signer=S.OutboundSigner(""))
    assert unsigned.verify_webhook(h, body) == (False, "missing_secret"), "没配密钥要拒收"
