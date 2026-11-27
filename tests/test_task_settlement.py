"""任务结算绑定(P0-03.3)回归。

锁住三件事:
  ① `settle_task_status` 的判定阶梯:COMPLETED / PARTIAL / FAILED / INTERRUPTED / BLOCKED;
     降级(STUB/DEGRADED)与「全部模型失败」文本绝不冒充 COMPLETED(M03/M04);
  ② 工具事实决定 PARTIAL:账本落账成功 + 分析未完成 → PARTIAL(M09,不靠字符串判成败);
  ③ `_run_task_core` 把统一状态写进 `resp["task_status"]` 并如实落到 task_finished,
     与既有 `resp["status"]`(skill_status / needs_confirmation)并存不覆盖。
"""
from __future__ import annotations

import asyncio
import types

from foxclaw.agents.base import CognitiveAgent
from foxclaw.core import call_status as cs
from foxclaw.core.call_status import (
    CallStatus, TaskCallCollector, settle_task_status,
)
from foxclaw.core.task_context import TaskContext


def _col(*states, trace=None):
    c = TaskCallCollector()
    for s in states:
        st = CallStatus()
        st.finish(s)
        c.add(st)
    if trace:
        c.add_trace(trace)
    return c


def _exec_steps(*verified):
    return [{"stage": "executed", "round": 0,
             "steps": [{"tool": "ledger", "stage": "succeeded", "verified": v}
                       for v in verified]}]


def test_real_and_fallback_are_completed():
    assert settle_task_status(_col(cs.REAL), "答复") == cs.COMPLETED
    assert settle_task_status(_col(cs.FALLBACK), "答复") == cs.COMPLETED


def test_all_providers_failed_is_failed():
    # M03:全是失败文本,绝不标真实完成
    assert settle_task_status(
        _col(cs.DEGRADED), "全部模型失败,本次回复不可信") == cs.FAILED


def test_stub_is_not_completed():
    # M04:Stub 占位文本不是业务结果
    assert settle_task_status(_col(cs.STUB), "【离线模式】你好") == cs.FAILED


def test_degraded_with_verified_tool_fact_is_partial():
    # M09:账本落账成功(工具事实在),分析未完成 → PARTIAL
    col = _col(cs.DEGRADED, trace=_exec_steps("verified"))
    assert settle_task_status(col, "已记账 105 元") == cs.PARTIAL


def test_degraded_with_unverified_tool_output_stays_failed():
    col = _col(cs.DEGRADED, trace=_exec_steps("failed"))
    assert settle_task_status(col, "已记账 105 元") == cs.FAILED


def test_real_with_tool_failure_is_partial():
    col = _col(cs.REAL)
    col.add_trace([{"stage": "failed", "round": 1, "error": "timeout"}])
    assert settle_task_status(col, "尽力了") == cs.PARTIAL


def test_interrupted_and_blocked_take_precedence():
    col = _col(cs.REAL)
    assert settle_task_status(col, "部分产物", interrupted=True) == cs.INTERRUPTED
    assert settle_task_status(col, "", blocked=True) == cs.BLOCKED


def test_no_llm_call_falls_back_to_result():
    empty = TaskCallCollector()
    assert settle_task_status(empty, {"ok": True}) == cs.COMPLETED
    assert settle_task_status(empty, "") == cs.FAILED


def test_pending_never_completes():
    col = TaskCallCollector()
    col.add(CallStatus())  # 未置终态 = 实现 bug,按降级处理
    assert col.has_pending() is True
    assert settle_task_status(col, "答复") == cs.FAILED


def _ns_agent(pipe, settled):
    ns = types.SimpleNamespace()
    ns.AGENT_ID = "base"
    ns.start_session = lambda goal, entities: "sess-t1"
    ns._pinned_session = lambda sid, ctx: sid
    ns._task_entities = lambda task: []
    ns._on_session_started = lambda *a, **k: None
    ns._prefs_context = lambda: ""
    ns._cognitive_pipeline = lambda: pipe
    ns._record_stage = lambda *a: None
    ns._emit_execution = lambda *a, **k: None
    ns._maybe_store_preference = lambda t: None
    # 【G2】TaskContext:假 Agent 也走真实状态对象(结算测试顺带锁住 task_context 与结算并存)
    ns._task_context_start = lambda sid, task, cons, resume_tid="", **_k: TaskContext.create(
        sid, task, cons)
    ns._save_task_state = lambda sid, tc: None
    ns._on_task_settled = lambda sid, status: settled.append((sid, status))
    ns.thought_chain = types.SimpleNamespace(get_summary=lambda aid: "")
    ns.router = types.SimpleNamespace()
    return ns


class _Pipe:
    def __init__(self, result, states=(), trace=None, interrupted=False):
        self.result = result
        self.states = states
        self.trace = trace
        self.interrupted = interrupted

    async def run(self, ctx, on_stage=None):
        col = cs.current_collector()
        for s in self.states:
            st = CallStatus()
            st.finish(s)
            col.add(st)
        if self.trace:
            col.add_trace(self.trace)
        ctx.result = self.result
        ctx.interrupted = self.interrupted


def test_run_task_core_writes_task_status():
    settled: list = []
    pipe = _Pipe("已处理", states=(cs.REAL,), trace=_exec_steps("verified"))
    resp = asyncio.run(CognitiveAgent._run_task_core(_ns_agent(pipe, settled), "做个预算"))
    assert resp["task_status"] == cs.COMPLETED
    assert resp["tool_trace"][0]["stage"] == "executed"
    assert settled == [("sess-t1", cs.COMPLETED)]


def test_run_task_core_partial_on_degraded_with_fact():
    settled: list = []
    pipe = _Pipe("已记账 105 元", states=(cs.DEGRADED,), trace=_exec_steps("verified"))
    resp = asyncio.run(CognitiveAgent._run_task_core(_ns_agent(pipe, settled), "记账并分析"))
    assert resp["task_status"] == cs.PARTIAL
    assert settled == [("sess-t1", cs.PARTIAL)]


def test_run_task_core_interrupted():
    settled: list = []
    pipe = _Pipe("前两步产物", states=(cs.REAL,), interrupted=True)
    resp = asyncio.run(CognitiveAgent._run_task_core(_ns_agent(pipe, settled), "长任务"))
    assert resp["task_status"] == cs.INTERRUPTED
    assert settled == [("sess-t1", cs.INTERRUPTED)]


def test_collector_is_reset_after_task():
    settled: list = []
    pipe = _Pipe("答复", states=(cs.REAL,))
    asyncio.run(CognitiveAgent._run_task_core(_ns_agent(pipe, settled), "x"))
    assert cs.current_collector() is None
