"""M11 并发交错的调用状态归属(双层隔离)回归。

Mock Provider + 同步屏障,不依赖真实 Key。两个变体分别锁住两层归属:
  变体 1:两个 Agent 共享同一 Router —— 验证 Router 级隔离(无全局降级标记串扰);
  变体 2:同一 Agent 实例并发跑 T1/T2 —— 验证 Agent 级隔离(无实例字段串扰)。

判据都落在「任务各自的收集器」与「task_status / 记忆准入」上,而不是 Router 状态。
"""
from __future__ import annotations

import asyncio
import threading
import types

from foxclaw.agents.mixins.llm import LlmMixin
from foxclaw.core import call_status as cs
from foxclaw.core.call_status import bind_collector, reset_collector


class _GatedRouter:
    """A 调用先挂起,B 正常完成,再由测试放行 A 完成降级填充。"""

    def __init__(self):
        self.a_entered = threading.Event()
        self.release_a = threading.Event()

    def supports_tools(self) -> bool:
        return False

    def complete(self, system, messages, max_tokens=2048, agent_id="", status=None, **kw):
        tag = str(messages[-1].get("content", ""))
        if tag.startswith("A"):
            self.a_entered.set()
            assert self.release_a.wait(5), "屏障超时"
            status.state = cs.DEGRADED
            status.error = "mock: all providers failed"
            return "全部模型失败"
        status.state = cs.REAL
        status.provider = "mock"
        return "答复 B"


class _LlmAgent(LlmMixin):
    AGENT_ID = "office"
    system_prompt = "sys"

    def __init__(self, router):
        self.router = router


async def _run_tagged(agent, tag):
    """每个任务自建收集器(任务级归属),返回 (收集器, 回复)。"""
    col, tok = bind_collector()
    try:
        reply = await agent._call_llm([{"role": "user", "content": tag}])
        return col, reply
    finally:
        reset_collector(tok)


def test_two_agents_share_router_do_not_cross_talk():
    router = _GatedRouter()
    a1, a2 = _LlmAgent(router), _LlmAgent(router)

    async def main():
        t_a = asyncio.create_task(_run_tagged(a1, "A: 核算这个月"))
        await asyncio.to_thread(router.a_entered.wait, 5)
        col_b, reply_b = await _run_tagged(a2, "B: 你好")
        assert reply_b == "答复 B"
        assert col_b.worst_state() == cs.REAL, "B 已完成且为 REAL,A 不得污染它"
        router.release_a.set()
        col_a, reply_a = await t_a
        return col_a, reply_a, col_b

    col_a, reply_a, col_b = asyncio.run(main())

    assert reply_a == "全部模型失败"
    assert col_a.worst_state() == cs.DEGRADED, "A 的降级只落在 A 的收集器"
    assert col_b.all_real() is True
    assert cs.settle_task_status(col_a, reply_a) == cs.FAILED
    assert cs.settle_task_status(col_b, "答复 B") == cs.COMPLETED
    # 记忆准入各自正确:B 放行,A 拦下
    assert cs.admit_memory(call_status=cs.CallStatus(state=col_b.worst_state())) is True
    assert cs.admit_memory(call_status=cs.CallStatus(state=col_a.worst_state())) is False


class _Pipe:
    """在 run 里做真实状态登记;可选屏障让两个任务交错消费。"""

    def __init__(self, state, entered=None, release=None):
        self.state = state
        self.entered = entered
        self.release = release

    async def run(self, ctx, on_stage=None):
        if self.entered is not None:
            self.entered.set()
            await asyncio.to_thread(self.release.wait, 5)
        col = cs.current_collector()
        st = cs.CallStatus()
        st.finish(self.state)
        col.add(st)
        ctx.result = ("失败" if self.state == cs.DEGRADED else "答复")


def _ns_agent(pipe, settled):
    ns = types.SimpleNamespace()
    ns.AGENT_ID = "office"
    ns.start_session = lambda goal, entities: f"sess-{id(ns)}"
    ns._pinned_session = lambda sid, ctx: str((ctx or {}).get("_session_id") or sid)
    ns._task_entities = lambda task: []
    ns._on_session_started = lambda *a, **k: None
    ns._prefs_context = lambda: ""
    ns._cognitive_pipeline = lambda: pipe
    ns._record_stage = lambda *a: None
    ns._emit_execution = lambda *a, **k: None
    ns._maybe_store_preference = lambda t: None
    # 【G2】TaskContext:假 Agent 也走真实状态对象
    from foxclaw.core.task_context import TaskContext
    ns._task_context_start = lambda sid, task, cons, resume_tid="", **_k: TaskContext.create(
        sid, task, cons)
    ns._save_task_state = lambda sid, tc: None
    ns._on_task_settled = lambda sid, status: settled.append((sid, status))
    ns.thought_chain = types.SimpleNamespace(get_summary=lambda aid: "")
    ns.router = types.SimpleNamespace()
    return ns


def test_same_agent_instance_two_tasks_are_isolated():
    from foxclaw.agents.base import CognitiveAgent

    entered = threading.Event()
    release = threading.Event()
    settled: list = []
    agent = _ns_agent(None, settled)

    async def main():
        t1 = asyncio.create_task(CognitiveAgent._run_task_core(
            agent, "T1 降级", context={"_session_id": "t1"}))
        await asyncio.to_thread(entered.wait, 5)
        agent._cognitive_pipeline = lambda: _Pipe(cs.REAL)
        r2 = await CognitiveAgent._run_task_core(
            agent, "T2 正常", context={"_session_id": "t2"})
        release.set()
        r1 = await t1
        return r1, r2

    agent._cognitive_pipeline = lambda: _Pipe(
        cs.DEGRADED, entered=entered, release=release)
    r1, r2 = asyncio.run(main())

    assert r1["task_status"] == cs.FAILED, "T1 降级 → FAILED,不被 T2 的成功带成 COMPLETED"
    assert r2["task_status"] == cs.COMPLETED, "T2 成功 → COMPLETED,不被 T1 的降级污染"
    assert ("t1", cs.FAILED) in [(s, st) for s, st in settled]
    assert ("t2", cs.COMPLETED) in [(s, st) for s, st in settled]
    assert cs.current_collector() is None
