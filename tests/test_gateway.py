"""统一 API 网关:per-client 令牌桶 + 配额 + 空闲淘汰 + 429/Retry-After + 启用哲学。
全离线(TestClient)。网关默认关,本测试显式 FOXCLAW_GATEWAY=1 + 低额度触发限流。"""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest

def test_gateway():
    # ISOLATION_GUARD:本用例依赖 import 时固化的模块级单例(网关/限流器/路由配置按 env 在 import 时定型),单进程全量跑会被前序用例污染 →
    # 假红。全量请用 tests/run_all.py(每文件独立子进程)。
    if "foxclaw.api" in sys.modules:
        pytest.skip("需独立进程运行(模块级单例已固化),请用 tests/run_all.py")
    # 本测试用 pop("FOXCLAW_API_KEY") 验证「未设即网关默认关」,但 pop 之后 load_dotenv
    # (override=False)仍会从 .env 把键灌回来(开发者 .env 常带真实 FOXCLAW_API_KEY)→ 判定
    # 随机器漂移。关掉 .env 使本测试自洽,单独运行也稳定。
    os.environ["FOXCLAW_SKIP_DOTENV"] = "1"
    # 必须在 import foxclaw.api 之前设好:网关实例在模块加载时按环境变量建
    os.environ["FOXCLAW_GATEWAY"] = "1"
    os.environ["FOXCLAW_GATEWAY_RATE"] = "5"        # 每分钟 5 → 桶容量 5,突发 5 后限流
    os.environ["FOXCLAW_GATEWAY_QUOTA"] = "1000"
    os.environ["FOXCLAW_MAX_BODY_MB"] = "1"          # 请求体硬顶降到 1MB 便于测 413
    os.environ.pop("FOXCLAW_API_KEY", None)
    os.environ["HKC_EMBEDDING"] = "stub"
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"
    os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_gw_")

    print("=" * 60); print("统一 API 网关验证"); print("=" * 60)

    # ── Part A:Gateway 类单元 ─────────────────────────────────
    from foxclaw.core.gateway import Gateway

    # [1] 突发令牌桶:容量内放行,超出限流(reason=rate)
    g = Gateway(rate_per_min=3, quota_per_hour=100000)
    allowed = [g.check("a")[0] for _ in range(5)]
    assert allowed[:3] == [True, True, True] and allowed[3] is False, allowed
    ok, retry, reason = g.check("a")
    assert not ok and reason == "rate" and retry >= 1, (ok, retry, reason)
    print("[1] 令牌桶突发限流(3 放行→限流,带 retry)OK")

    # [2] per-client 隔离:a 被限,b 不受影响
    assert g.check("b")[0] is True, "客户端之间不隔离"
    print("[2] per-client 隔离 OK")

    # [3] 配额(固定窗口):超每小时额度 → reason=quota
    g2 = Gateway(rate_per_min=100000, quota_per_hour=4)
    cnt = sum(1 for _ in range(6) if g2.check("z")[0])
    assert cnt == 4, cnt
    ok, retry, reason = g2.check("z")
    assert not ok and reason == "quota" and retry >= 1, (ok, retry, reason)
    print("[3] 每小时配额封顶(reason=quota)OK")

    # [4] 空闲淘汰:大量不同 client 不撑爆内存
    g3 = Gateway(rate_per_min=100, quota_per_hour=100000, max_clients=64)
    for i in range(500):
        g3.check(f"c{i}")
    assert g3.stats()["tracked_clients"] <= 64, g3.stats()
    print(f"[4] 空闲淘汰(500 client → 追踪 {g3.stats()['tracked_clients']}≤64,防内存 DoS)OK")

    # ── Part B:接入 FastAPI 中间件(TestClient)────────────────
    from fastapi.testclient import TestClient
    from foxclaw.api import app, _gateway_on

    with TestClient(app) as c:
        # [5] stats 端点:启用 + 额度可见
        s = c.get("/api/gateway/stats").json()
        assert s["enabled"] is True and s["rate_per_min"] == 5, s
        print(f"[5] /api/gateway/stats(enabled={s['enabled']}, rate={s['rate_per_min']}/min)OK")

        # [6] 超限 → 429 + Retry-After(同一 client=ip:testclient 共用桶)
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

        # [7] 探针豁免:/health 不在网关下,始终放行
        assert c.get("/health").status_code == 200, "探针被网关误限"
        print("[7] 探针 /health 豁免网关 OK")

        # [8] 请求体硬顶:Content-Length 超 1MB → 413(在读 body/sanitize 前拦下,始终生效)
        big = c.post("/api/knowledge/ingest", json={"text": "x" * (1200 * 1024)})
        assert big.status_code == 413, (big.status_code, big.text[:120])
        print("[8] 超大请求体 → 413(读 body 前拦截)OK")

        # [8b] chunked / 无 Content-Length:只查头会漏,流上累计必须兜住
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
        _wrap_body_limit(req, 15)          # 第 2 块累计 20 > 15

        async def _drain():
            for _ in range(3):
                await req.receive()

        try:
            asyncio.run(_drain())
        except _PayloadTooLarge:
            pass
        else:
            raise AssertionError("chunked 超限未被流上计数拦住")

        # [8c] 流超限转成 413,而不是冒成 500
        async def _boom(_req):
            raise _PayloadTooLarge(999)

        req2 = _Req({"type": "http", "method": "POST", "path": "/api/x",
                     "scheme": "http", "server": ("test", 80),
                     "headers": [], "query_string": b""})
        resp = asyncio.run(_gateway_mw(req2, _boom))
        assert resp.status_code == 413, resp.status_code
        print("[8b] chunked 超限 → 流上计数拦截并转 413 OK")

    # [9] 启用哲学:默认关;配 FOXCLAW_API_KEY(=对外暴露)自动开;=0 强制关优先
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
