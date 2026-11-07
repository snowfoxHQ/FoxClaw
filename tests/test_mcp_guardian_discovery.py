"""第4步 · MCP 发现阶段进守门人:主进程(内存+磁盘)不碰 headers 凭证。"""
import sys, os, tempfile
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_mcp_guardian_discovery():
    os.environ["FOXCLAW_SKIP_DOTENV"] = "1"
    os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_mcp_guardian_")

    # 1. tool_to_spec 兼容 dict(守门人序列化产物),fail-closed 不变
    from foxclaw.skills.mcp_client import tool_to_spec
    spec = tool_to_spec("srv", {"name": "get_time", "description": "读时间", "inputSchema": {},
                                "annotations": {"readOnlyHint": True, "destructiveHint": None}})
    assert spec.id == "mcp__srv__get_time" and spec.side_effect == "read" and not spec.destructive
    spec2 = tool_to_spec("srv", {"name": "do_thing", "description": "", "inputSchema": {},
                                 "annotations": None})
    assert spec2.side_effect == "write"  # fail-closed:无注解按有副作用
    print("[1] tool_to_spec 兼容 dict + fail-closed OK")

    # 2. 守门人 mcp_register:主进程空 headers 不覆盖守门人已有凭证
    import foxclaw.kernel.sentinel as sm
    servers = {}
    sm._handle_mcp_register({"servers": [
        {"id": "a", "name": "a", "url": "https://a", "headers": {"Authorization": "Bearer SECRET"},
         "transport": "streamable_http"}]}, servers)
    assert servers["a"]["headers"]["Authorization"] == "Bearer SECRET"
    sm._handle_mcp_register({"servers": [
        {"id": "a", "name": "a", "url": "https://a", "transport": "streamable_http"}]}, servers)
    assert servers["a"]["headers"]["Authorization"] == "Bearer SECRET", "空 headers 不应覆盖守门人已有凭证"
    print("[2] mcp_register 空 headers 保留旧凭证 OK")

    # 3. 守门人 mcp_list_tools 代发现:用自己的 headers 连 + 序列化为 dict
    import foxclaw.skills.mcp_client as mc
    _seen = {}
    async def fake_list(url, headers, transport):
        _seen["headers"] = headers
        return [NS(name="t1", description="d", inputSchema={},
                   annotations=NS(readOnlyHint=True, destructiveHint=None))]
    mc._amcp_list_tools = fake_list
    out = sm._handle_mcp_list_tools({"server_id": "a"}, servers)
    assert out["tools"][0]["name"] == "t1"
    assert out["tools"][0]["annotations"]["readOnlyHint"] is True
    assert _seen["headers"].get("Authorization") == "Bearer SECRET"  # 守门人用自己的凭证连
    print("[3] mcp_list_tools 守门人代发现 + 序列化 + 凭证不出守门人 OK")

    # 4. mcp_add_server_guardian:主进程不存 headers,守门人收到 headers
    class FakeSentinel:
        def __init__(self):
            self.registered = None
        def mcp_register(self, servers):
            self.registered = servers
        def mcp_list_tools(self, sid):
            return [{"name": "t1", "description": "", "inputSchema": {}, "annotations": None}]

    class FakeMgr:
        def __init__(self):
            self._servers = {}
        def servers(self):
            return [dict(s) for s in self._servers.values()]
        def restore(self, cfg):
            self._servers[cfg["id"]] = cfg

    class FakeStore:
        def __init__(self):
            self.saved = None
        def save(self, servers):
            self.saved = servers

    class FakeHost:
        pass

    host = FakeHost()
    host.sentinel = FakeSentinel()
    host.mcp_manager = FakeMgr()
    host.mcp_store = FakeStore()

    from foxclaw import Foxclaw
    Foxclaw.mcp_add_server_guardian(host, {
        "id": "srv", "name": "srv", "url": "https://srv",
        "headers": {"X-Key": "SECRET"}, "transport": "streamable_http"})

    assert "headers" not in host.mcp_manager.servers()[0], "主进程 McpManager 不应存 headers"
    assert "headers" not in host.mcp_store.saved[0], "主进程磁盘不应存 headers"
    assert host.sentinel.registered[-1]["headers"]["X-Key"] == "SECRET", "守门人应收到 headers"
    print("[4] mcp_add_server_guardian:主进程内存/磁盘无 headers,守门人持 headers OK")

    print("\n第4步 · MCP 发现进守门人 验证全部通过")

if __name__ == "__main__":
    test_mcp_guardian_discovery()
