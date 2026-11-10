"""LLM 流式输出(方案A)验证:router.stream 分块 + /api/chat/stream SSE 逐字返回。"""
import os, sys, tempfile, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest


def test_streaming():
    if "foxclaw.api" in sys.modules:
        pytest.skip("需独立进程运行(模块级单例已固化),请用 tests/run_all.py")
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"
    os.environ.setdefault("FOXCLAW_EMBEDDING_MODEL", "foxclaw-nonexistent-fast")
    os.environ["FOXCLAW_STREAM_RATE"] = "1"
    os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_stream_")

    print("=" * 60); print("LLM 流式输出验证"); print("=" * 60)

    from foxclaw.core.model_router import ModelRouter
    r = ModelRouter()
    chunks = list(r.stream("sys", [{"role": "user", "content": "你好世界这是一段用于测试流式分块的文本"}]))
    assert len(chunks) > 1, f"应多块: {len(chunks)}"
    assert "".join(chunks), "拼接应非空"
    print(f"[1] router.stream stub 分块(chunk={len(chunks)}) OK")

    from fastapi.testclient import TestClient
    from foxclaw.api import app
    with TestClient(app) as c:
        with c.stream("POST", "/api/chat/stream",
                      json={"task": "简单介绍一下你自己", "agent_id": "supervisor"}) as resp:
            assert resp.status_code == 200, resp.status_code
            deltas, done = [], False
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                if "delta" in ev:
                    deltas.append(ev["delta"])
                if ev.get("done"):
                    done = True
            assert len(deltas) > 0, "应有 delta 事件"
            assert done, "应以 done 收尾"
            assert "".join(deltas), "拼接应非空"
        print(f"[2] /api/chat/stream SSE(delta={len(deltas)}, done) OK")

        with c.stream("POST", "/api/chat/stream",
                      json={"task": "再来一次", "agent_id": "supervisor"}) as resp:
            txt = "".join(resp.iter_text())
            assert "rate limited" in txt, f"限流未生效: {txt[:120]}"
        print("[3] /api/chat/stream 令牌桶限流 OK")

    print("\nLLM 流式输出验证全部通过")


if __name__ == "__main__":
    test_streaming()
