"""计划可见:plan 节点入链 + SSE `{"plan":…}` 事件 + 阻塞返回体带 plan。

背景:`stage_plan` 早就算出了计划(steps/flow),但只写 `ctx.plan` 供审计,既不落 thought
chain 也不送前端,调用方"跑完才知道它打算怎么做"。本组用例锁住三条出口:
  ① stage_plan 落 `plan` 类型节点(metadata 带 steps);
  ② run_task_stream 在 plan stage 后补发 `{"plan": …}`;
  ③ 阻塞路径 `_run_task_core` 的返回体带 `plan`(由 `_plan_payload` 统一提取)。
均**不改变执行**:act 仍整段交给 LLM,计划不驱动执行。
"""

from __future__ import annotations

import types

from foxclaw.agents.base import CognitiveAgent, _plan_payload
from foxclaw.pipeline.cognitive_pipeline import _record_plan_node, stage_plan


def _ctx(task, agent=None):
    from foxclaw.pipeline import PipelineContext
    return PipelineContext(agent=agent or types.SimpleNamespace(), task=task)


class _ThinkRec:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return None, None


def test_stage_plan_records_plan_node() -> None:
    rec = _ThinkRec()
    ctx = _ctx("先查资料然后写总结", agent=types.SimpleNamespace(_think=rec))
    stage_plan(ctx)
    assert ctx.plan["steps"], "plan stage 仍应算出步骤"
    plans = [c for c in rec.calls if c.get("node_type") == "plan"]
    assert len(plans) == 1, "应落恰好一条 plan 节点"
    node = plans[0]
    assert node["metadata"]["steps"] == ctx.plan["steps"]
    assert node["metadata"]["flow"] == ctx.plan["flow"]
    assert node["content"].startswith("计划[")


def test_record_plan_node_never_breaks_execution() -> None:
    _record_plan_node(_ctx("x", agent=None))          # 无 _think → 静默跳过

    def _boom(**kwargs):
        raise RuntimeError("harness 挂了")

    _record_plan_node(_ctx("x", agent=types.SimpleNamespace(_think=_boom)))  # 不得抛出


def test_plan_payload_accepts_both_shapes() -> None:
    domain = types.SimpleNamespace(plan={"steps": ["a", "b"], "flow": "sequential",
                                         "multi_step": True})
    assert _plan_payload(domain) == {"steps": ["a", "b"], "flow": "sequential",
                                     "multi_step": True}
    sup = types.SimpleNamespace(plan_obj=types.SimpleNamespace(steps=["x", "y"], flow="parallel"))
    assert _plan_payload(sup)["steps"] == ["x", "y"]
    assert _plan_payload(sup)["multi_step"] is True      # 未声明 → 按步数推断
    one = types.SimpleNamespace(plan_obj=types.SimpleNamespace(steps=["x"]))
    assert _plan_payload(one)["multi_step"] is False
    assert _plan_payload(types.SimpleNamespace(plan=None)) is None
    assert _plan_payload(types.SimpleNamespace()) is None


def test_stream_emits_plan_event_after_plan_stage() -> None:
    ctx = types.SimpleNamespace(plan={"steps": ["a", "b"], "flow": "sequential",
                                      "multi_step": True})

    async def _core(task, context=None, stop_event=None, on_stage=None):
        on_stage("recall", ctx)
        on_stage("plan", ctx)          # 管线在 plan stage 完成后回调
        return {"result": "done"}

    fake = types.SimpleNamespace(_run_task_core=_core)
    events = list(CognitiveAgent.run_task_stream(fake, "任务"))
    assert {"stage": "recall"} in events
    assert {"stage": "plan"} in events
    plans = [e for e in events if "plan" in e]
    assert plans == [{"plan": {"steps": ["a", "b"], "flow": "sequential",
                               "multi_step": True}}], "plan 事件应紧跟 plan stage 且只发一次"
    assert events.index({"stage": "plan"}) < events.index(plans[0]), "plan 事件必须在 stage 之后"
    assert events[-1] == {"result": {"result": "done"}}, "流式末尾必带 result 事件"
