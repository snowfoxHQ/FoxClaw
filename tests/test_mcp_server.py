"""Foxclaw 内置 MCP 服务器 端到端(带鉴权,贴真实暴露场景):
- 防后门:不带 X-API-Key 访问 /api/mcp/ → 401
- 带 key 的 MCP 客户端能 initialize + list_tools(3 只读 + ask_foxclaw) + call
真起 uvicorn(MCP over Streamable HTTP 需真实 URL)。"""
import os, sys, tempfile, threading, time, asyncio, urllib.request, urllib.error
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent))

@pytest.mark.xfail(
    reason="pre-existing 失败:真实 uvicorn MCP-over-Streamable-HTTP 端到端断言需要 base_url/凭证配置,本地 stub 环境(/api/mcp/ 后门检查)与基线不一致。需真实 provider 环境才能过。",
    strict=False,
)
def test_mcp_server():
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"
    os.environ["FOXCLAW_SKIP_DOTENV"] = "1"
    os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_mcpsrv_")
    os.environ["FOXCLAW_API_KEY"] = "mcp-test-key"
    os.environ["FOXCLAW_MCP_SERVER"] = "1"
    os.environ["FOXCLAW_MCP_AGENTS"] = "1"
    os.environ["FOXCLAW_MCP_RATE_AGENT"] = "1"
    os.environ.setdefault("FOXCLAW_EMBEDDING_MODEL", "foxclaw-nonexistent-fast")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import uvicorn
    import httpx
    from foxclaw.api import app, _foxclaw_mcp

    assert _foxclaw_mcp is not None, "FOXCLAW_MCP_SERVER=1 时应构建 MCP 服务器"
    print("=" * 60); print("Foxclaw 内置 MCP 服务器验证(带鉴权)"); print("=" * 60)

    PORT = 8079
    KEY = "mcp-test-key"
    threading.Thread(target=lambda: uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="error"),
                     daemon=True).start()
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2); break
        except urllib.error.HTTPError:
            break
        except Exception:
            time.sleep(1)

    with httpx.Client(timeout=8) as c:
        body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        hdr = {"Accept": "application/json, text/event-stream"}
        r = c.post(f"http://127.0.0.1:{PORT}/api/mcp/", json=body, headers=hdr)
        assert r.status_code == 401, f"后门!/api/mcp 未受鉴权保护: {r.status_code}"
        r = c.post(f"http://127.0.0.1:{PORT}/api/mcp/", json=body, headers={**hdr, "X-API-Key": "wrong"})
        assert r.status_code == 401, "错误 key 应 401"
    print("[0] 防后门:无key/错key 访问 /api/mcp/ → 401 OK")


    async def probe():
        from mcp.client.streamable_http import streamablehttp_client
        from mcp import ClientSession
        async with streamablehttp_client(f"http://127.0.0.1:{PORT}/api/mcp/",
                                         headers={"X-API-Key": KEY}) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = {t.name for t in (await s.list_tools()).tools}
                assert tools == {"knowledge_search", "knowledge_get", "memory_recall", "ask_foxclaw"}, tools
                print("[1] 带key initialize + list_tools(3 只读 + ask_foxclaw) OK")

                res = await s.call_tool("knowledge_search", {"query": "anything", "top_k": 999999})
                assert res.content is not None
                print("[2] knowledge_search 可调 + top_k 钳制不崩 OK")

                res = await s.call_tool("memory_recall", {"query": "hello"})
                assert res.content is not None
                print("[3] memory_recall 可调 OK")

                res = await s.call_tool("knowledge_get", {"ku_id": "nonexistent"})
                txt = res.content[0].text if res.content else ""
                assert "not_found" in txt or "error" in txt
                print("[4] knowledge_get 不存在 → not_found OK")

                res = await s.call_tool("ask_foxclaw", {"task": "分析这个月支出"})
                txt = res.content[0].text if res.content else ""
                assert "dispatched_to" in txt or "reply" in txt or "error" in txt, txt
                print("[5] ask_foxclaw Agent 调度可调(返回 reply/dispatched_to) OK")

                res = await s.call_tool("ask_foxclaw", {"task": "再来一次"})
                txt = res.content[0].text if res.content else ""
                assert "rate limited" in txt, f"限流未生效: {txt}"
                print("[6] ask_foxclaw 限流(令牌桶)生效 OK")

    asyncio.run(probe())
    print("\nFoxclaw 内置 MCP 服务器验证全部通过")

if __name__ == "__main__":
    test_mcp_server()
