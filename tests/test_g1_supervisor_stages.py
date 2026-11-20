"""G1 主管入口替换测试：命中/未命中/指纹/版本/规划保护/零 LLM 调用。"""
import asyncio
import hashlib
from types import SimpleNamespace

from foxclaw.pipeline.supervisor_stages import sv_dispatch, sv_plan, _g1_whitelist_match

TASKSET = "g0d-taskset-0.1.2"
TASK = "计算 128 × 64 的结果，只输出数字。"


def _ctx(g1=None, task=TASK, self_answer=True):
    return SimpleNamespace(task=task, context={"_g1": g1} if g1 else {},
                           extra={"_self_answer": self_answer}, agent=None,
                           sched=None, dispatch_result=None, result=None)


def _g1(**kw):
    base = {"enabled": True, "task_id": "T3-001", "taskset_version": TASKSET,
            "include_t3_007": False}
    base.update(kw)
    if "fingerprint" not in base:
        raw = "\x1f".join((base["taskset_version"], base["task_id"], TASK))
        base["fingerprint"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return base


class _Agent:
    def __init__(self):
        self.llm_calls = 0
        self.web_calls = 0
        self.plan_calls = 0
        self.memories = []

    async def _call_llm(self, msgs):
        self.llm_calls += 1
        return "LLM 结果"

    def _web_augment(self, task, msgs):
        self.web_calls += 1
        return msgs

    def _plan_task(self, task, sched):
        self.plan_calls += 1
        return None, None

    def _store_memory(self, **kw):
        self.memories.append(kw)

    def _think(self, **kw):
        pass

    @property
    def _subagents(self):
        return {}


def _run(coro):
    return asyncio.run(coro)


def test_whitelist_hit_executes_python_without_llm_or_web():
    agent = _Agent()
    g = _g1()
    ctx = _ctx(g)
    ctx.agent = agent
    _run(sv_dispatch(ctx))
    assert ctx.dispatch_result["result"] == {
        "dispatched_to": "supervisor", "sub_result": "8192", "self_answered": True}
    assert isinstance(ctx.dispatch_result["result"]["sub_result"], str)
    assert (agent.llm_calls, agent.web_calls) == (0, 0)
    assert g["execution_status"] == "SUCCESS" and g["web_augment"] == "skipped"
    assert g["shared_memory"] == "skipped" and agent.memories == []


def test_no_whitelist_uses_original_self_answer_path():
    agent = _Agent()
    ctx = _ctx(self_answer=True)  # 无 _g1
    ctx.agent = agent
    _run(sv_dispatch(ctx))
    assert ctx.dispatch_result["result"]["sub_result"] == "LLM 结果"
    # 自答路径不再做任何联网增强;web_calls 作为哨兵,若被调用即回归
    assert (agent.llm_calls, agent.web_calls) == (1, 0)


def test_fingerprint_mismatch_is_input_invalid_no_fallback():
    agent = _Agent()
    g = _g1(fingerprint="bad-fingerprint")
    ctx = _ctx(g)
    ctx.agent = agent
    _run(sv_dispatch(ctx))
    assert g["execution_status"] == "INPUT_INVALID"
    assert agent.llm_calls == 0 and agent.memories == []
    assert ctx.dispatch_result["result"]["sub_result"] == ""


def test_taskset_version_mismatch_rejected():
    g = _g1(taskset_version="g0d-taskset-OLD")
    assert _g1_whitelist_match(_ctx(g))["reason"] == "TASKSET_MISMATCH"


def test_t3_007_conditional_and_finance_routes_covered():
    assert _g1_whitelist_match(_ctx(_g1(task_id="T3-007"))) is None
    assert _g1_whitelist_match(_ctx(_g1(task_id="T3-007", include_t3_007=True)))["matched"]
    # T3-003/009 在 G0 经 finance 派发；G1 入口检查覆盖它们
    assert _g1_whitelist_match(_ctx(_g1(task_id="T3-003")))["matched"]
    assert _g1_whitelist_match(_ctx(_g1(task_id="T3-009")))["matched"]


def test_sv_plan_protection_and_no_regression():
    agent = _Agent()
    g = _g1()
    ctx = _ctx(g, self_answer=False)
    ctx.agent = agent
    _run(sv_plan(ctx))
    assert agent.plan_calls == 0
    plain = _ctx(self_answer=False)
    plain.agent = agent
    _run(sv_plan(plain))
    assert agent.plan_calls == 1
