"""Foxclaw 内置 MCP 服务器:把只读知识(HKC)+记忆(HMR)暴露成 MCP 工具,
让任何 MCP 客户端(Claude Desktop/Cursor/别的 Agent)能调用个人知识与记忆。
默认关(FOXCLAW_MCP_SERVER=1 开),挂 /api/mcp/,受 X-API-Key 鉴权。
安全:knowledge_*/memory_recall 只读;ask_foxclaw(需再开 FOXCLAW_MCP_AGENTS)会驱动 Agent
     并写入记忆/审计,非只读。工具名/描述中性,错误对外脱敏(不泄露内部细节/架构)。"""
from __future__ import annotations
import os
import time
import threading
from mcp.server.fastmcp import FastMCP

_MAX_TOP_K = 50
_MAX_QUERY = 2000


class _RateLimiter:
    """令牌桶限流(线程安全)。防外部刷爆:读工具与 ask_foxclaw 各一桶。"""
    def __init__(self, per_min: int):
        self.capacity = max(1, int(per_min))
        self.tokens = float(self.capacity)
        self.rate = self.capacity / 60.0
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def allow(self) -> bool:
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
            self.last = now
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _clamp_top_k(v, default: int = 5) -> int:
    try:
        return max(1, min(int(v), _MAX_TOP_K))
    except Exception:
        return default


def _clean_query(q) -> str:
    return (str(q or "")[:_MAX_QUERY])


def _external_action(sid: str):
    """给「非技能动作」(如外部驱动 Agent)做个最小 skill 替身,好走同一套权限门。

    鸭子类型,只提供 permission.evaluate 真正读的字段;不 import skills 包(避免启停期的
    包级循环)。side_effect=write + external=True 是事实描述:它确实有副作用且面向外部。
    """
    from types import SimpleNamespace
    return SimpleNamespace(id=sid, kind="mcp", params={"task": {"required": True}},
                           side_effect="write", external=True,
                           destructive=False, read_only=False)


def _policy_error(gate, rules, task: str) -> str | None:
    """外部驱动动作的准入裁决。返回 None = 放行,否则返回错误码。

    为什么这里要单独判:外部客户端「绕开工具直接驱动 Agent」,若不过门,宿主配的
    deny(封杀某动作)和 ask(要求人工)对它完全无效。
    MCP 没有交互确认通道 → 命中 ask 规则即拒,而不是放行后等一个永远不会来的确认;
    其余常规 ask(副作用 / external)由 FOXCLAW_MCP_AGENTS=1 这道显式开关承担授权。
    """
    if gate is None:
        return None
    from foxclaw.skills.permission import ASK_RULE, MISSING_PARAMS
    v = gate.check_action(_external_action("mcp:ask_foxclaw"), {"task": task},
                          rules=rules, mode="strict", confirmed=False)
    code = getattr(v.raw, "reason_code", "") or ""
    if getattr(v, "level", "") == "deny":
        return "blocked"
    if code == ASK_RULE:
        return "needs_confirmation"
    if code == MISSING_PARAMS:
        return "missing arguments"
    return None


def build_foxclaw_mcp(os_getter, expose_agents: bool = False) -> FastMCP:
    """os_getter:无参回调,返回当前 Foxclaw 实例(惰性,启动后才有)。
    expose_agents:另开一道开关才暴露 Agent 调度(有副作用面,默认关)。"""
    mcp = FastMCP("Foxclaw Knowledge", stateless_http=True, streamable_http_path="/")
    # 限流(env 可配):读工具较宽松、ask_foxclaw(驱动 LLM,贵)较紧
    read_limiter = _RateLimiter(_env_int("FOXCLAW_MCP_RATE_READ", 120))
    agent_limiter = _RateLimiter(_env_int("FOXCLAW_MCP_RATE_AGENT", 20))

    @mcp.tool()
    def knowledge_search(query: str, top_k: int = 5, domain: str = "") -> list:
        """在个人知识库中检索相关知识条目(只读)。返回每条的 id/名称/摘要/类型。"""
        if not read_limiter.allow():
            return [{"error": "rate limited"}]
        try:
            hkc = getattr(os_getter(), "hkc", None)
            if hkc is None or not getattr(hkc, "available", False):
                return []
            hits = hkc.client.search(_clean_query(query), mode="hybrid",
                                     top_k=_clamp_top_k(top_k), domain=str(domain or "")[:120])
            return [{"id": getattr(h, "ku_id", "") or getattr(h, "id", ""),
                     "name": getattr(h, "name", ""),
                     "summary": getattr(h, "summary", ""),
                     "type": str(getattr(h, "ku_type", ""))}
                    for h in (hits or [])]
        except Exception:
            return [{"error": "internal error"}]        # 对外脱敏:不回传 str(e)

    @mcp.tool()
    def knowledge_get(ku_id: str) -> dict:
        """按 id 取一个知识条目的详情(只读)。"""
        if not read_limiter.allow():
            return {"error": "rate limited"}
        try:
            hkc = getattr(os_getter(), "hkc", None)
            if hkc is None or not getattr(hkc, "available", False):
                return {"error": "knowledge base unavailable"}
            ku = hkc.container.graph_store.get(str(ku_id or "")[:200])
            if ku is None:
                return {"error": "not_found"}
            return {"id": getattr(ku, "ku_id", ku_id), "name": getattr(ku, "name", ""),
                    "summary": getattr(ku, "summary", ""), "type": str(getattr(ku, "ku_type", "")),
                    "domain": getattr(ku, "domain", ""), "tags": list(getattr(ku, "tags", []) or [])}
        except Exception:
            return {"error": "internal error"}

    @mcp.tool()
    def memory_recall(query: str, top_k: int = 5) -> list:
        """在个人记忆中召回相关内容(只读;仅助手层+共享层)。"""
        if not read_limiter.allow():
            return [{"error": "rate limited"}]
        try:
            hmr = getattr(os_getter(), "hmr", None)
            if hmr is None:
                return []
            res = hmr.recall(query=_clean_query(query), agent_id="supervisor", top_k=_clamp_top_k(top_k))
            return [{"id": r.get("id", ""), "content": r.get("content", ""),
                     "type": r.get("type", "")} for r in (res or [])]
        except Exception:
            return [{"error": "internal error"}]

    if expose_agents:
        @mcp.tool()
        async def ask_foxclaw(task: str, agent: str = "supervisor") -> dict:
            """把一个任务交给 Foxclaw 的认知 Agent 处理(主管默认自动分诊到财务/行政/办公/助手)。
            返回 {reply, dispatched_to}。
            ⚠ 非只读:执行过程会写入个人记忆(HMR)与审计链。有副作用的**技能**调用(写/发送/支付)
            会被内部权限门拦下需确认,MCP 无法确认→不执行;但记忆写入等内部副作用仍会发生。
            因此这是"写能力"工具,仅在 FOXCLAW_MCP_AGENTS=1 且鉴权下暴露。"""
            if not agent_limiter.allow():
                return {"error": "rate limited"}
            import asyncio as _a
            os_ = os_getter()
            if os_ is None:
                return {"error": "not ready"}
            task = _clean_query(task)
            agent = str(agent or "supervisor")[:40]
            if not task.strip():
                return {"error": "missing arguments"}
            err = _policy_error(getattr(os_, "policy_gate", None),
                                getattr(getattr(os_, "skill_adapter", None), "rules", None),
                                task)
            if err:
                return {"error": err}

            def _work():
                from foxclaw.agents.base import _suppress_memory
                _suppress_memory.set(True)   # 外部驱动的任务不写 HMR(防污染),随上下文传到 run_task
                return os_.scheduler.run(
                    lambda: _a.run(os_.run(task, agent)),
                    agent_id=agent, task=task)
            try:
                res = await _a.to_thread(_work)
            except Exception:
                return {"error": "internal error"}
            if not isinstance(res, dict):
                return {"reply": str(res), "dispatched_to": agent}
            reply = (res.get("sub_result") or res.get("summary")
                     or res.get("reply") or res.get("error") or "")
            return {"reply": str(reply), "dispatched_to": res.get("dispatched_to", agent)}

    return mcp
