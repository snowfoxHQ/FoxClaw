"""MCP 连接器(HTTP Streamable/SSE 远程)。mcp SDK 是 async,本模块用后台事件循环线程
把它桥成同步,供 SkillAdapter(sync,跨请求/后台线程)调用。按需连接:每次开→用→关。
仅远程传输,无本地代码执行。"""
from __future__ import annotations
import asyncio
import threading
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.sse import sse_client


# ── async→sync 桥:单例后台 loop 线程 ──────────────────────────
class _Bridge:
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        t = threading.Thread(target=self._run, daemon=True, name="mcp-loop")
        t.start()

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro, timeout: float = 35):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return fut.result(timeout=timeout)
        except BaseException:
            fut.cancel()   # 超时/异常必须取消协程,否则挂起的连接会一直留在 loop 里泄漏
            raise


_bridge = None
_bridge_lock = threading.Lock()


def _get_bridge() -> _Bridge:
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = _Bridge()
        return _bridge


# ── SSRF 防线:连接(含重定向的每一跳)都校验目标 host 不是内网/本地/元数据 ──────
async def _ssrf_request_hook(request):
    """httpx 请求钩子:对每个即将发出的请求(初始 + 重定向 follow-up)校验 host。
    堵 redirect-SSRF + DNS 重绑定 + 配置漂移(恶意 server 把你导去内网/云元数据)。"""
    from .review import _is_internal_host
    host = (request.url.host or "").lower()
    if await asyncio.to_thread(_is_internal_host, host):
        raise RuntimeError(f"MCP 连接被拒:目标解析到内网/本地/元数据地址({host})")


def _safe_httpx_factory(headers=None, timeout=None, auth=None):
    import httpx
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout if timeout is not None else httpx.Timeout(30.0),
        auth=auth,
        follow_redirects=True,                          # 允许(合法同源尾斜杠跳转),但每跳过钩子
        event_hooks={"request": [_ssrf_request_hook]},
    )


# ── 底层 async 助手(测试可 monkeypatch 这两个) ─────────────────
@asynccontextmanager
async def _connect(url, headers, transport):
    if transport == "sse":
        async with sse_client(url, headers=headers, httpx_client_factory=_safe_httpx_factory) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                yield s
    else:
        async with streamablehttp_client(url, headers=headers, httpx_client_factory=_safe_httpx_factory) as (read, write, _):
            async with ClientSession(read, write) as s:
                await s.initialize()
                yield s


async def _amcp_list_tools(url, headers, transport):
    async with _connect(url, headers, transport) as s:
        return list((await s.list_tools()).tools)


async def _amcp_call_tool(url, headers, transport, name, args):
    async with _connect(url, headers, transport) as s:
        r = await s.call_tool(name, args or {})
        text = "".join(getattr(c, "text", "") for c in (r.content or []))
        return {"isError": bool(getattr(r, "isError", False)), "content": text,
                "structured": getattr(r, "structuredContent", None)}


def _norm(cfg: dict) -> dict:
    return {"id": cfg["id"], "name": cfg.get("name") or cfg["id"], "url": cfg["url"],
            "headers": dict(cfg.get("headers") or {}),
            "transport": cfg.get("transport") or "streamable_http"}


# ── 工具 → SkillSpec(fail-closed) ─────────────────────────────
def _get(tool, key, default=None):
    """兼容 dict(守门人序列化后) 与 mcp.types.Tool 对象(直连回退路径)。"""
    return tool.get(key, default) if isinstance(tool, dict) else getattr(tool, key, default)


def tool_to_spec(server_id: str, tool):
    from . import SkillSpec
    ann = _get(tool, "annotations", None)
    if isinstance(ann, dict):
        ro = ann.get("readOnlyHint")
        de = ann.get("destructiveHint")
    else:
        ro = getattr(ann, "readOnlyHint", None) if ann else None
        de = getattr(ann, "destructiveHint", None) if ann else None
    if ro is True:
        se, destr = "read", False
    elif de is True:
        se, destr = "write", True
    else:
        se, destr = "write", False          # fail-closed:无注解按有副作用
    tname = _get(tool, "name", "") or ""
    return SkillSpec(
        id=f"mcp__{server_id}__{tname}", name=tname,
        description=_get(tool, "description", "") or "", kind="mcp",
        mcp_server=server_id, mcp_tool=tname, side_effect=se, destructive=destr,
        external=True, params=_get(tool, "inputSchema", {}) or {},
        triggers=[tname], source="user",
    )


# ── 供 SkillAdapter 的客户端代理 ──────────────────────────────
class _ClientProxy:
    def __init__(self, mgr, sid):
        self._mgr, self._sid = mgr, sid

    def call_tool(self, tool, args):
        cfg = self._mgr._servers.get(self._sid)
        if not cfg:
            raise RuntimeError(f"MCP server {self._sid} 未登记")
        from foxclaw.core.tracing import span as _span, current_traceparent
        with _span("mcp.call_tool", server=self._sid, tool=str(tool)):
            # 跨进程传播:带上 W3C traceparent,对端(外部 MCP server)若支持即可接上同一条链。
            # 这是 Foxclaw 唯一真实的跨进程边界,也是"分布式追踪"在本系统里唯一有意义之处。
            headers = dict(cfg["headers"] or {})
            tp = current_traceparent()
            if tp:
                headers["traceparent"] = tp
            out = _get_bridge().run(_amcp_call_tool(cfg["url"], headers, cfg["transport"], tool, args or {}))
        if out.get("isError"):
            raise RuntimeError(out.get("content") or "MCP 工具返回错误")
        return out


class McpManager:
    """服务器配置 + 连接。实现 .get(id) 供 SkillAdapter.mcp_clients 用。"""
    def __init__(self):
        self._servers: dict = {}
        self._lock = threading.RLock()

    def restore(self, cfg: dict):
        with self._lock:
            self._servers[cfg["id"]] = _norm(cfg)

    def add_server(self, cfg: dict):
        c = _norm(cfg)
        tools = _get_bridge().run(_amcp_list_tools(c["url"], c["headers"], c["transport"]))
        with self._lock:
            self._servers[c["id"]] = c
        return tools

    def list_tools(self, sid: str):
        with self._lock:
            c = self._servers.get(sid)
        if not c:
            raise KeyError(sid)
        return _get_bridge().run(_amcp_list_tools(c["url"], c["headers"], c["transport"]))

    def remove_server(self, sid: str) -> bool:
        with self._lock:
            return self._servers.pop(sid, None) is not None

    def servers(self) -> list:
        with self._lock:
            return [dict(c) for c in self._servers.values()]

    def get(self, sid: str):
        with self._lock:
            has = sid in self._servers
        return _ClientProxy(self, sid) if has else None
