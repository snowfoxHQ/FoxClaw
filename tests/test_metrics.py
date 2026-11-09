"""ops 监控:请求/LLM 指标(计数/错误率/延迟分位)+ 深度健康 + Prometheus 文本。"""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest


def test_metrics():
    if "foxclaw.api" in sys.modules:
        pytest.skip("需独立进程运行(模块级单例已固化),请用 tests/run_all.py")
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"
    os.environ.setdefault("FOXCLAW_EMBEDDING_MODEL", "foxclaw-nonexistent-fast")
    os.environ["HKC_EMBEDDING"] = "stub"
    os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_metrics_")

    from fastapi.testclient import TestClient
    from foxclaw.api import app

    print("=" * 60); print("ops 监控验证"); print("=" * 60)

    with TestClient(app) as c:
        c.get("/api/status"); c.get("/api/ahs/skills"); c.get("/health")
        c.post("/api/task", json={"task": "测试", "agent_id": "supervisor"})

        hd = c.get("/health/ready")
        assert hd.status_code == 200, hd.status_code
        j = hd.json()
        assert j["status"] in ("ok", "degraded") and "hmr" in j["components"] and "router" in j["components"], j
        assert j["components"]["router"] in ("ok", "stub", "down"), "router 不应泄露 provider 名"
        print(f"[1] /health/ready 深度健康({j['status']},{len(j['components'])} 组件,router 不泄露) OK")

        m = c.get("/api/metrics").json()
        assert "uptime_s" in m and m["endpoints"], m
        ep = next(k for k in m["endpoints"] if "status" in k)
        st = m["endpoints"][ep]
        assert st["count"] >= 1 and "p95_ms" in st and "error_rate" in st and "avg_ms" in st, st
        print(f"[2] /api/metrics 端点(p50={st['p50_ms']} p95={st['p95_ms']} err={st['error_rate']}) OK")

        assert m["llm"], f"LLM 指标应非空: {m['llm']}"
        print(f"[3] LLM 埋点({list(m['llm'])}) OK")

        p = c.get("/metrics")
        assert p.status_code == 200 and "foxclaw_requests_total" in p.text and "foxclaw_llm_calls_total" in p.text
        print("[4] /metrics Prometheus 文本 OK")

        for i in range(20):
            c.get(f"/api/does-not-exist/random-{i}")
        m2 = c.get("/api/metrics").json()
        rand_keys = [k for k in m2["endpoints"] if "random-" in k]
        assert len(rand_keys) == 0, f"404 路径不该各自成 key(内存DoS): {rand_keys[:3]}"
        assert any("unmatched" in k for k in m2["endpoints"]), "应有 unmatched 汇总 key"
        print("[5] 404 路径归入 unmatched(防指标 key 内存 DoS) OK")

    print("\nops 监控验证全部通过")


if __name__ == "__main__":
    test_metrics()
