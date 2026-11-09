"""工具中心第2期 · MCP 连接器 端到端验证(monkeypatch async 助手,不联网)。"""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_mcp_connectors():
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"
    os.environ["FOXCLAW_SKIP_DOTENV"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    _DATA = tempfile.mkdtemp(prefix="foxclaw_mcp_")
    os.environ["FOXCLAW_DATA_DIR"] = _DATA

    from fastapi.testclient import TestClient
    from foxclaw.api import app
    import foxclaw.skills.mcp_client as mc
    from types import SimpleNamespace as NS

    import asyncio as _aio
    _blocked = False
    try:
        _aio.run(mc._amcp_list_tools("http://127.0.0.1:9/mcp", {}, "streamable_http"))
    except BaseException as _e:
        _s = str(_e) + repr(getattr(_e, "exceptions", ""))
        _blocked = ("内网" in _s) or ("127.0.0.1" in _s) or ("拒" in _s)
    assert _blocked, "客户端 SSRF 钩子未拦截内网连接"
    print("[SSRF] 客户端连内网被请求钩子拦截 OK")

    FAKE_TOOLS = [
        NS(name="get_time", description="读时间", inputSchema={}, annotations=NS(readOnlyHint=True, destructiveHint=None)),
        NS(name="do_thing", description="干活", inputSchema={}, annotations=None),
    ]
    async def fake_list(url, headers, transport): return list(FAKE_TOOLS)
    async def fake_call(url, headers, transport, name, args): return {"isError": False, "content": "ok:" + name, "structured": None}
    mc._amcp_list_tools = fake_list
    mc._amcp_call_tool = fake_call

    SRV = {"id": "demo_srv", "name": "演示", "url": "https://mcp.example.com/mcp", "acknowledged": True}

    with TestClient(app) as c:
        print("=" * 60); print("工具中心 · MCP 连接器 验证"); print("=" * 60)

        r = c.post("/api/ahs/mcp/servers", json=SRV)
        assert r.status_code == 200, r.text
        assert r.json()["tools_registered"] == 2
        print("[1] 加服务器 + 注册 2 工具 OK")

        skills = {s["id"]: s for s in c.get("/api/ahs/skills").json()["skills"]}
        assert skills["mcp__demo_srv__get_time"]["side_effect"] == "read"
        assert skills["mcp__demo_srv__do_thing"]["side_effect"] == "write"
        print("[2] 工具成技能 + fail-closed(read/write) OK")

        srvs = c.get("/api/ahs/mcp/servers").json()["servers"]
        assert srvs and srvs[0]["tool_count"] == 2
        print("[3] 服务器列表 tool_count=2 OK")

        r = c.post("/api/ahs/mcp/servers", json={"id": "bad", "url": "http://127.0.0.1/mcp"})
        assert r.status_code == 403, r.text
        print("[4] 审核门 deny 内网(403) OK")

        r = c.post("/api/ahs/mcp/servers", json={"id": "p2", "url": "https://mcp2.example.com/mcp"})
        assert r.status_code == 409 and r.json()["detail"]["needs_ack"] is True
        print("[5] 外部低信任需确认(409) OK")

        r = c.post("/api/ahs/skills/mcp__demo_srv__get_time/test", json={"args": {}})
        assert r.json()["status"] == "needs_confirmation", r.text
        r = c.post("/api/ahs/skills/mcp__demo_srv__get_time/test", json={"args": {}, "confirmed": True})
        j = r.json()
        assert r.status_code == 200 and j["success"] is True, r.text
        out = j["output"]
        assert out.get("_untrusted") is True, out
        assert "不可信" in out.get("data", {}).get("content", ""), out
        print("[6] 测试调用 mcp 技能 + 双层 untrusted 标记(防提示注入) OK")

        async def boom(url, headers, transport): raise RuntimeError("connrefused")
        mc._amcp_list_tools = boom
        r = c.post("/api/ahs/mcp/servers", json={"id": "err_srv", "url": "https://mcp3.example.com/mcp", "acknowledged": True})
        assert r.status_code == 502, r.text
        mc._amcp_list_tools = fake_list
        print("[7] 连接失败 502 OK")

        assert c.delete("/api/ahs/mcp/servers/demo_srv").status_code == 200
        assert "mcp__demo_srv__get_time" not in {s["id"] for s in c.get("/api/ahs/skills").json()["skills"]}
        print("[8] 移除服务器 + 技能消失 OK")

    with TestClient(app) as c:
        c.post("/api/ahs/mcp/servers", json=SRV)
    from foxclaw import Foxclaw
    os2 = Foxclaw(data_dir=_DATA)
    assert any(s["id"] == "demo_srv" for s in os2.mcp_manager.servers()), "server 未持久化"
    assert os2.skill_registry.get("mcp__demo_srv__get_time") is not None, "mcp 技能未持久化"
    print("[9] 重启后 server + mcp 技能仍在(持久化 round-trip) OK")

    print("\nMCP 连接器验证全部通过")

if __name__ == "__main__":
    test_mcp_connectors()
