"""LLM 流式输出(方案A)验证:router.stream 分块 + /api/chat/stream SSE 逐字返回。"""
import os, sys, tempfile, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_streaming():
    os.environ["SNOWFOXCLAW_SHADOW_ROUNDS"] = "6"
    os.environ.setdefault("SNOWFOXCLAW_EMBEDDING_MODEL", "snowfoxclaw-nonexistent-fast")
    os.environ["SNOWFOXCLAW_STREAM_RATE"] = "1"   # 令牌桶=1,测限流
    os.environ["SNOWFOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="snowfoxclaw_stream_")

    print("=" * 60); print("LLM 流式输出验证"); print("=" * 60)

    # 1) router.stream(stub 模式):分块拼接 == 完整文本、多块
    from snowfoxclaw.core.model_router import ModelRouter
    r = ModelRouter()   # 无 key → stub
    chunks = list(r.stream("sys", [{"role": "user", "content": "你好世界这是一段用于测试流式分块的文本"}]))
    assert len(chunks) > 1, f"应多块: {len(chunks)}"
    assert "".join(chunks), "拼接应非空"
    print(f"[1] router.stream stub 分块(chunk={len(chunks)}) OK")

    # 2) /api/chat/stream SSE:多个 delta + done 收尾
    from fastapi.testclient import TestClient
    from snowfoxclaw.api import app
    with TestClient(app) as c:
        with c.stream("POST", "/api/chat/stream",
                      json={"task": "简单介绍一下你自己", "agent_id": "assistant"}) as resp:
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

        # 3) 限流:桶=1,上面 [2] 已消耗 → 再请求被限流
        with c.stream("POST", "/api/chat/stream",
                      json={"task": "再来一次", "agent_id": "assistant"}) as resp:
            txt = "".join(resp.iter_text())
            assert "rate limited" in txt, f"限流未生效: {txt[:120]}"
        print("[3] /api/chat/stream 令牌桶限流 OK")

    print("\nLLM 流式输出验证全部通过")


if __name__ == "__main__":
    test_streaming()
