"""统一 API 网关:per-client 令牌桶 + 配额 + 空闲淘汰 + 429/Retry-After + 启用哲学。
全离线(TestClient)。网关默认关,本测试显式 FOXCLAW_GATEWAY=1 + 低额度触发限流。"""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest

def test_gateway():
    if "foxclaw.api" in sys.modules:
        pytest.skip("需独立进程运行(模块级单例已固化),请用 tests/run_all.py")
    os.environ["FOXCLAW_SKIP_DOTENV"] = "1"
    os.environ["FOXCLAW_GATEWAY"] = "1"
    os.environ["FOXCLAW_GATEWAY_RATE"] = "5"
    os.environ["FOXCLAW_GATEWAY_QUOTA"] = "1000"
    os.environ["FOXCLAW_MAX_BODY_MB"] = "1"
    os.environ.pop("FOXCLAW_API_KEY", None)
    os.environ["HKC_EMBEDDING"] = "stub"
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"
    os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_gw_")

    print("=" * 60); print("统一 API 网关验证"); print("=" * 60)

    from foxclaw.core.gateway import Gateway

    g = Gateway(rate_per_min=3, quota_per_hour=100000)
    allowed = [g.check("a")[0] for _ in range(5)]
    assert allowed[:3] == [True, True, True] and allowed[3] is False, allowed
    ok, retry, reason = g.check("a")
    assert not ok and reason == "rate" and retry >= 1, (ok, retry, reason)
    print("[1] 令牌桶突发限流(3 放行→限流,带 retry)OK")

    assert g.check("b")[0] is True, "客户端之间不隔离"
    print("[2] per-client 隔离 OK")

    g2 = Gateway(rate_per_min=100000, quota_per_hour=4)
    cnt = sum(1 for _ in range(6) if g2.check("z")[0])
    assert cnt == 4, cnt
    ok, retry, reason = g2.check("z")
    assert not ok and reason == "quota" and retry >= 1, (ok, retry, reason)
    print("[3] 每小时配额封顶(reason=quota)OK")

    g3 = Gateway(rate_per_min=100, quota_per_hour=100000, max_clients=64)
    for i in range(500):
        g3.check(f"c{i}")
    assert g3.stats()["tracked_clients"] <= 64, g3.stats()
    print(f"[4] 空闲淘汰(500 client → 追踪 {g3.stats()['tracked_clients']}≤64,防内存 DoS)OK")

    from fastapi.testclient import TestClient
    from foxclaw.api import app, _gateway_on

    with TestClient(app) as c:
        s = c.get("/api/gateway/stats").json()
        assert s["enabled"] is True and s["rate_per_min"] == 5, s
        print(f"[5] /api/gateway/stats(enabled={s['enabled']}, rate={s['rate_per_min']}/min)OK")

        codes = [c.get("/api/status").status_code for _ in range(12)]
        assert 200 in codes and 429 in codes, codes
        r429 = None
        for _ in range(15):
            r = c.get("/api/status")
            if r.status_code == 429:
                r429 = r; break
        assert r429 is not None, "未触发 429"
        assert r429.headers.get("Retry-After") and r429.json().get("retry_after", 0) >= 1, (r429.headers, r429.text)
        print("[6] 突发超限 → 429 + Retry-After 头 OK")

        assert c.get("/health").status_code == 200, "探针被网关误限"
        print("[7] 探针 /health 豁免网关 OK")

        big = c.post("/api/knowledge/ingest", json={"text": "x" * (1200 * 1024)})
        assert big.status_code == 413, (big.status_code, big.text[:120])
        print("[8] 超大请求体 → 413(读 body 前拦截)OK")

        import asyncio
        from starlette.requests import Request as _Req
        from foxclaw.api.middleware import (
            _gateway_mw, _PayloadTooLarge, _wrap_body_limit)

        pending = [b"x" * 10, b"y" * 10, b"z" * 10]

        async def _recv():
            if pending:
                return {"type": "http.request", "body": pending.pop(0),
                        "more_body": bool(pending)}
            return {"type": "http.disconnect"}

        req = _Req({"type": "http", "method": "POST", "path": "/api/x",
                    "scheme": "http", "server": ("test", 80),
                    "headers": [], "query_string": b""}, _recv)
        _wrap_body_limit(req, 15)

        async def _drain():
            for _ in range(3):
                await req.receive()

        try:
            asyncio.run(_drain())
        except _PayloadTooLarge:
            pass
        else:
            raise AssertionError("chunked 超限未被流上计数拦住")

        async def _boom(_req):
            raise _PayloadTooLarge(999)

        req2 = _Req({"type": "http", "method": "POST", "path": "/api/x",
                     "scheme": "http", "server": ("test", 80),
                     "headers": [], "query_string": b""})
        resp = asyncio.run(_gateway_mw(req2, _boom))
        assert resp.status_code == 413, resp.status_code
        print("[8b] chunked 超限 → 流上计数拦截并转 413 OK")

    os.environ.pop("FOXCLAW_GATEWAY", None); os.environ.pop("FOXCLAW_API_KEY", None)
    assert _gateway_on() is False, "本地默认应关"
    os.environ["FOXCLAW_API_KEY"] = "x"
    assert _gateway_on() is True, "配鉴权应自动开"
    os.environ["FOXCLAW_GATEWAY"] = "0"
    assert _gateway_on() is False, "FOXCLAW_GATEWAY=0 应强制关"
    os.environ.pop("FOXCLAW_GATEWAY", None); os.environ.pop("FOXCLAW_API_KEY", None)
    print("[9] 启用哲学(默认关 / 配 key 自动开 / =0 强制关)OK")

    print("\n统一 API 网关验证全部通过")

if __name__ == "__main__":
    test_gateway()
