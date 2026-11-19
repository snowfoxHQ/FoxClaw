"""批次三·P0-03 验收矩阵 M08/M09/M10/M12 + M11 补充(对话流式路径)。

M01-M06 在 test_call_status.py,M07 在 test_memory_admission.py,M11 并发交错在
test_concurrent_call_isolation.py;本文件补齐没有落到具体调用点的四项,以及 M11
基准里「同时覆盖 base.py 对话流式路径的记忆准入」这半句。
"""
from __future__ import annotations

import asyncio
import types

import pytest

from foxclaw.agents import base as base_mod
from foxclaw.agents.impl.finance import FinanceAgent
from foxclaw.agents.impl.office import OfficeAgent
from foxclaw.agents.impl.supervisor import SupervisorAgent
from foxclaw.agents.mixins import tool_exec
from foxclaw.core import call_status as cs
from foxclaw.core import tools
from foxclaw.core.call_status import CallStatus, bind_collector, reset_collector


def _degraded_ctx():
    """任务上下文里已有一次降级调用。"""
    col, tok = bind_collector()
    st = CallStatus()
    st.finish(cs.DEGRADED)
    col.add(st)
    return col, tok


def _real_ctx():
    col, tok = bind_collector()
    st = CallStatus()
    st.finish(cs.REAL)
    col.add(st)
    return col, tok


# ---------- M08:办公学习 Stub 产出的知识 JSON 不入库 ----------

_KNOWLEDGE_JSON = (
    '复利就是利滚利。\n'
    '{"new_knowledge": [{"title": "复利", "content": "复利=本金产生收益后再计息",'
    ' "tags": ["finance"]}]}'
)


def _office(store, response=_KNOWLEDGE_JSON):
    ns = types.SimpleNamespace()
    ns._is_schedule_task = lambda t: False
    ns._strip_knowledge_json = OfficeAgent._strip_knowledge_json
    ns._think = lambda **kw: None
    ns._store_memory = lambda **kw: store.append(kw)
    ns.learn_rt = None
    ns.itin = None

    async def _call(*a, **k):
        return response

    ns._call_llm_web = _call
    return ns


@pytest.mark.parametrize("state", [cs.STUB, cs.DEGRADED])
def test_m08_stub_or_degraded_knowledge_not_stored(state):
    store: list = []
    col, tok = bind_collector()
    try:
        st = CallStatus()
        st.finish(state)
        col.add(st)
        resp, meta = asyncio.run(
            OfficeAgent._execute(_office(store), "讲讲复利", {}, "s1", []))
    finally:
        reset_collector(tok)
    assert store == [], f"{state} 的模型产物被当成长期知识写入"
    assert "new_knowledge" not in resp, "结构化块应被剥离,不直接抛给用户"
    assert meta["new_knowledge_stored"] == 1, "识别计数照实,只是不准入"


def test_m08_real_knowledge_is_stored():
    store: list = []
    col, tok = _real_ctx()
    try:
        resp, meta = asyncio.run(
            OfficeAgent._execute(_office(store), "讲讲复利", {}, "s1", []))
    finally:
        reset_collector(tok)
    assert len(store) == 1 and store[0]["mem_type"] == "concept"
    assert store[0]["title"] == "复利"
    assert "new_knowledge" not in resp
    assert meta["new_knowledge_stored"] == 1


# ---------- M09:确定性记账成功 + 模型失败 ----------

class _Fin:
    def __init__(self, rec):
        self.rec = rec

    def record_text(self, task):
        return self.rec


_LEDGER_OK = {"recorded": [{"amount": -105.0}], "ack": "已记账 1 笔", "dry_run": False}


def _finance(store, rec=_LEDGER_OK, response="分析结论:本月偏紧"):
    ns = types.SimpleNamespace()
    ns.fin = _Fin(rec)
    ns._think = lambda **kw: None
    ns._store_memory = lambda **kw: store.append(kw)
    ns._auto_record = lambda t: FinanceAgent._auto_record(ns, t)

    async def _call(*a, **k):
        return response

    ns._call_llm_web = _call
    return ns


def test_m09_ledger_kept_analysis_blocked_and_partial():
    store: list = []
    col, tok = _degraded_ctx()
    try:
        resp, _ = asyncio.run(FinanceAgent._execute(
            _finance(store), "今天买了一件衣服花了105块,帮我分析这个月花销", {}, "s1", []))
        status = cs.settle_task_status(col, resp)
    finally:
        reset_collector(tok)

    assert any(r["mem_type"] == "execution" for r in store), "真实落账必须保留"
    assert not any(r["mem_type"] == "decision" for r in store), "降级分析结论不得入库"
    assert status == cs.PARTIAL, "账本已落账 + 分析未完成 = PARTIAL,既不是 COMPLETED 也不是 FAILED"


def test_m09_dry_run_ledger_is_not_fact():
    """预览(未落账)不算事实:既不写记忆,也不让任务升到 PARTIAL。"""
    store: list = []
    rec = {"recorded": [{"amount": -105.0}], "ack": "预览(未落账) 1 笔", "dry_run": True}
    col, tok = _degraded_ctx()
    try:
        resp, _ = asyncio.run(FinanceAgent._execute(
            _finance(store, rec=rec), "今天买衣服花了105块,帮我分析", {}, "s1", []))
        status = cs.settle_task_status(col, resp)
    finally:
        reset_collector(tok)
    assert store == []
    assert status == cs.FAILED


# ---------- M10:三 Agent 的状态与准入规则一致 ----------

def _supervisor(store, response="方案:先记账再复盘"):
    ns = types.SimpleNamespace()
    ns._think = lambda **kw: None
    ns._store_memory = lambda **kw: store.append(kw)

    async def _call(*a, **k):
        return response

    ns._call_llm_web = _call
    return ns


def test_m10_three_agents_share_one_admission_rule():
    finance_store: list = []
    office_store: list = []
    supervisor_store: list = []

    col, tok = _degraded_ctx()
    try:
        asyncio.run(FinanceAgent._execute(
            _finance(finance_store), "帮我分析这个月花销", {}, "s1", []))
        asyncio.run(OfficeAgent._execute(
            _office(office_store), "讲讲复利", {}, "s1", []))
        asyncio.run(SupervisorAgent._dispatch_fallback(
            _supervisor(supervisor_store), "整理季度计划", {}))
        worst = col.worst_state()
    finally:
        reset_collector(tok)

    assert finance_store == [] and office_store == [] and supervisor_store == []
    assert worst == cs.DEGRADED, "三 Agent 读同一个任务收集器,状态必然一致"


def test_m10_three_agents_all_admit_when_real():
    finance_store: list = []
    office_store: list = []
    supervisor_store: list = []

    col, tok = _real_ctx()
    try:
        asyncio.run(FinanceAgent._execute(
            _finance(finance_store), "帮我分析这个月花销", {}, "s1", []))
        asyncio.run(OfficeAgent._execute(
            _office(office_store), "讲讲复利", {}, "s1", []))
        asyncio.run(SupervisorAgent._dispatch_fallback(
            _supervisor(supervisor_store), "整理季度计划", {}))
    finally:
        reset_collector(tok)

    assert len(finance_store) == 1 and len(office_store) == 1 and len(supervisor_store) == 1


# ---------- M12:工具执行成功,最终模型调用失败 ----------

class _ToolRouter:
    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.i = 0

    def supports_tools(self) -> bool:
        return True

    def call_tools(self, system, messages, schemas, agent_id="", status=None, **kw):
        r = self.rounds[min(self.i, len(self.rounds) - 1)]
        self.i += 1
        if r.get("error"):
            if status is not None:
                status.finish(cs.DEGRADED)
            return {"supported": True, "error": r["error"]}
        if status is not None:
            status.finish(cs.REAL)
        return {"supported": True,
                "tool_calls": r.get("tool_calls") or [],
                "text": r.get("text", ""),
                "assistant_msg": {"role": "assistant", "content": r.get("text", "")}}

    def format_tool_result(self, call, result):
        return {"role": "tool", "content": result}


class _Registry:
    def schemas(self):
        return [{"type": "function",
                 "function": {"name": "web_search", "parameters": {}}}]


def _tool_agent(rounds):
    ns = types.SimpleNamespace()
    ns.AGENT_ID = "office"
    ns.system_prompt = "sys"
    ns.router = _ToolRouter(rounds)
    return ns


def _patch_tools(monkeypatch, result="【联网搜索结果】结论 https://example.com/a"):
    """工具实际返回可核验内容(带来源链接)→ 后置核验为 verified。"""
    monkeypatch.setattr(tools, "default_registry", lambda: _Registry())
    monkeypatch.setattr(
        tool_exec, "_run_tools_batch",
        lambda agent, calls, states=None: [result] * len(calls))


_TOOL_ROUND = {"tool_calls": [{"name": "web_search", "args": {}, "id": "c1"}]}


def test_m12_tool_fact_preserved_when_final_model_fails(monkeypatch):
    _patch_tools(monkeypatch)
    agent = _tool_agent([_TOOL_ROUND, {"error": "api down"}])
    col, tok = _degraded_ctx()
    try:
        reply = tool_exec.ToolExecMixin._reply_with_tools(
            agent, "查一下今天天气", [{"role": "user", "content": "查一下今天天气"}])
        trace = [t["stage"] for t in reply.tool_trace]
        status = cs.settle_task_status(col, reply.text)
    finally:
        reset_collector(tok)

    assert reply.state == cs.DEGRADED
    assert trace == ["requested", "executed", "failed"], trace
    executed = [t for t in reply.tool_trace if t["stage"] == "executed"][0]
    assert executed["steps"][0]["verified"] == "verified", "工具真实执行证据必须保留"
    assert "未完成" in reply.text and "已完成" not in reply.text
    assert status == cs.PARTIAL, "工具有真实产出但解释未完成 → PARTIAL"


def test_m12_rejected_tool_is_not_a_fact(monkeypatch):
    """工具被拒(未核验)→ 没有工具事实 → 全败即 FAILED。"""
    _patch_tools(monkeypatch, result="操作被拒绝:需要用户确认后才能执行")
    agent = _tool_agent([_TOOL_ROUND, {"error": "api down"}])
    col, tok = _degraded_ctx()
    try:
        reply = tool_exec.ToolExecMixin._reply_with_tools(
            agent, "查一下今天天气", [{"role": "user", "content": "查一下今天天气"}])
        status = cs.settle_task_status(col, reply.text)
    finally:
        reset_collector(tok)
    assert reply.state == cs.DEGRADED
    assert status == cs.FAILED


def test_m12_tool_fact_with_final_success_is_completed(monkeypatch):
    _patch_tools(monkeypatch)
    agent = _tool_agent([_TOOL_ROUND, {"text": "今天晴,25 度"}])
    col, tok = _real_ctx()
    try:
        reply = tool_exec.ToolExecMixin._reply_with_tools(
            agent, "查一下今天天气", [{"role": "user", "content": "查一下今天天气"}])
        status = cs.settle_task_status(col, reply.text)
    finally:
        reset_collector(tok)
    assert reply.state == cs.REAL and reply.text == "今天晴,25 度"
    assert [t["stage"] for t in reply.tool_trace] == ["requested", "executed"]
    assert status == cs.COMPLETED


# ---------- M11 补充:base.py 对话流式路径的记忆准入 ----------

class _StreamRouter:
    content_guard = None

    def __init__(self, state):
        self.state = state
        self.degraded_reason = "boom" if state == cs.DEGRADED else ""

    def supports_tools(self) -> bool:
        return False

    def stream(self, system, messages, agent_id="", stop_event=None, images=None,
               on_thinking=None, status=None):
        if status is not None:
            status.state = self.state
            status.error = self.degraded_reason
        yield "答复内容"


def _chat_agent(store, state):
    ns = types.SimpleNamespace()
    ns.AGENT_ID = "base"
    ns.system_prompt = "sys"
    ns.router = _StreamRouter(state)
    ns._caps = None
    ns._sanitize_history = lambda h: []
    ns._think = lambda **kw: None
    ns._store_memory = lambda *a, **k: store.append((a, k))
    ns._maybe_store_preference = lambda t: None
    return ns


@pytest.mark.parametrize("state,expected", [
    (cs.REAL, 1), (cs.FALLBACK, 1), (cs.DEGRADED, 0), (cs.STUB, 0),
])
def test_m11_stream_reply_memory_admission(state, expected):
    store: list = []
    col, tok = bind_collector()
    try:
        out = "".join(base_mod.CognitiveAgent.stream_reply(_chat_agent(store, state), "你好"))
        worst = col.worst_state()
    finally:
        reset_collector(tok)
    assert out == "答复内容"
    assert len(store) == expected, f"{state} 的直答记忆准入判定不对"
    assert worst == state


if __name__ == "__main__":
    import pytest as _pytest
    raise SystemExit(_pytest.main([__file__, "-q"]))
