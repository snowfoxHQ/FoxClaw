"""
LLM 驱动规划器(P0-1)测试 —— 全离线(用 fake router,不触发真实网络)。

覆盖:
  1. 开关:FOXCLAW_LLM_PLAN / FOXCLAW_LLM_REPLAN 默认关 / 显式开
  2. build_plan_tool:submit_plan 的 schema 含 agent 枚举 + flow 枚举
  3. llm_plan 的 fail-safe:不支持 tools / 模型不产出 / args 解析失败 / 空输入 / 抛异常 → None
  4. llm_plan 正常解析:flow + steps + 依赖 + 非法 agent 兜底 + 单步强制 single
  5. SupervisorAgent._llm_plan:未开启/不支持/单步 → None;多步 → (Plan, step_routes)
  6. llm_replan:正常产出修正计划 / 空 steps=放弃 / fail-safe → None
  7. _dispatch_failed:blocked/failed 失败判定
  8. _replan_and_redo:失败 → 反思 → 重执行 → 成功(含放弃短路)
  9. sv_replan stage:无失败/未开启 短路保留原结果
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.planning.llm_planner import (
    llm_plan,
    llm_plan_enabled,
    llm_replan,
    replan_enabled,
    build_plan_tool,
)


class _FakeRouter:
    """fake ModelRouter:只暴露 llm_plan 依赖的接口。"""

    def __init__(self, is_stub=False, supports=True, result=None):
        self.is_stub = is_stub
        self._supports = supports
        self._result = result
        self.last_call = None

    def supports_tools(self):
        return self._supports and not self.is_stub

    def call_tools(self, system, messages, tools, tool_choice="auto",
                   max_tokens=1024, agent_id=""):
        self.last_call = {"system": system, "messages": messages,
                          "tools": tools, "tool_choice": tool_choice}
        return self._result


def _catalog():
    return {"finance": "财务", "office": "办公", "admin": "行政", "assistant": "助手"}


def _plan_args(flow="sequential", steps=()):
    return {"id": "c1", "name": "submit_plan",
            "args": {"flow": flow, "steps": list(steps)}}


def test_enabled_switch():
    print("\n【1】开关默认关 / 显式开")
    os.environ.pop("FOXCLAW_LLM_PLAN", None)
    assert llm_plan_enabled() is False, "默认必须关(可逆/不改变现有行为)"
    os.environ["FOXCLAW_LLM_PLAN"] = "1"
    assert llm_plan_enabled() is True
    os.environ["FOXCLAW_LLM_PLAN"] = "0"
    assert llm_plan_enabled() is False
    os.environ.pop("FOXCLAW_LLM_PLAN", None)
    print("  ✓ 默认关 / '1'开 / '0'关")


def test_build_plan_tool_schema():
    print("\n【2】submit_plan schema:agent 枚举约束")
    tool = build_plan_tool(_catalog())
    fn = tool["function"]
    assert tool["type"] == "function"
    assert fn["name"] == "submit_plan"
    agent_enum = fn["parameters"]["properties"]["steps"]["items"]["properties"]["agent"]["enum"]
    assert set(agent_enum) == {"finance", "office", "admin", "assistant"}, agent_enum
    flow_enum = fn["parameters"]["properties"]["flow"]["enum"]
    assert set(flow_enum) == {"single", "sequential", "parallel"}
    print("  ✓ schema 含 agent 枚举 + flow 枚举")


def test_llm_plan_failsafe():
    print("\n【3】fail-safe:不支持/不产出/解析失败/空输入/抛异常 → None")
    # 不支持 tools
    r = _FakeRouter(result={"supported": False})
    assert llm_plan(r, "任务", _catalog()) is None
    # 模型没调用工具(空 tool_calls)
    r2 = _FakeRouter(result={"supported": True, "tool_calls": []})
    assert llm_plan(r2, "任务", _catalog()) is None
    # args 解析失败
    r3 = _FakeRouter(result={"supported": True, "tool_calls": [
        {"id": "c1", "name": "submit_plan", "args": {}, "args_parse_failed": True}]})
    assert llm_plan(r3, "任务", _catalog()) is None
    # 空 catalog / 空 task
    assert llm_plan(r2, "任务", {}) is None
    assert llm_plan(r2, "", _catalog()) is None
    # call_tools 抛异常 → None(不冒泡)

    class _Boom:
        def call_tools(self, *a, **k):
            raise RuntimeError("boom")

    assert llm_plan(_Boom(), "任务", _catalog()) is None
    print("  ✓ 6 条 fail-safe 路径全部回退 None")


def test_llm_plan_parse():
    print("\n【4】正常解析:flow + steps + 依赖 + 非法 agent 兜底")
    r = _FakeRouter(result={"supported": True, "tool_calls": [
        _plan_args("sequential", [
            {"name": "查支出", "agent": "finance", "depends_on": []},
            {"name": "定预算", "agent": "finance", "depends_on": [0]},
            {"name": "发邮件", "agent": "office", "depends_on": [1]},
        ])]})
    out = llm_plan(r, "查支出再定预算再发邮件", _catalog())
    assert out["flow"] == "sequential"
    assert [s["name"] for s in out["steps"]] == ["查支出", "定预算", "发邮件"]
    assert [s["agent"] for s in out["steps"]] == ["finance", "finance", "office"]
    assert out["steps"][1]["depends_on"] == [0]

    # 单步 → flow 强制 single
    r2 = _FakeRouter(result={"supported": True, "tool_calls": [
        _plan_args("parallel", [{"name": "查天气", "agent": "office"}])]})
    out2 = llm_plan(r2, "查天气", _catalog())
    assert out2["flow"] == "single" and len(out2["steps"]) == 1

    # 非法 agent(含已下放的 assistant/admin)兜底 office
    r3 = _FakeRouter(result={"supported": True, "tool_calls": [
        _plan_args("single", [{"name": "x", "agent": "不存在的Agent"}])]})
    out3 = llm_plan(r3, "x", _catalog())
    assert out3["steps"][0]["agent"] == "office"
    print("  ✓ 解析 / 单步single / 非法agent兜底 正确")


def test_supervisor_llm_plan():
    print("\n【5】SupervisorAgent._llm_plan:开关 + 路由链路")
    from foxclaw.agents.agents import SupervisorAgent
    D = type("D", (), {"AGENT_NAME": "dummy"})
    sub = {"finance": D(), "office": D()}

    # 未开启 → None
    os.environ.pop("FOXCLAW_LLM_PLAN", None)
    a1 = SupervisorAgent.__new__(SupervisorAgent)
    a1.router = _FakeRouter(result={"supported": True})
    a1._subagents = sub
    assert a1._llm_plan("任务") is None

    # 开启 + 多步 → (Plan, step_routes)
    os.environ["FOXCLAW_LLM_PLAN"] = "1"
    a2 = SupervisorAgent.__new__(SupervisorAgent)
    a2.router = _FakeRouter(result={"supported": True, "tool_calls": [
        _plan_args("sequential", [
            {"name": "查支出", "agent": "finance"},
            {"name": "发邮件", "agent": "office"},
        ])]})
    a2._subagents = sub
    plan, routes = a2._llm_plan("查支出再发邮件")
    assert plan is not None and plan.flow_type == "sequential"
    assert len(plan.tasks) == 2
    assert routes == [("查支出", "finance"), ("发邮件", "office")]

    # 单步 → None(交回关键词路由,不改变单步行为)
    a3 = SupervisorAgent.__new__(SupervisorAgent)
    a3.router = _FakeRouter(result={"supported": True, "tool_calls": [
        _plan_args("single", [{"name": "查天气", "agent": "office"}])]})
    a3._subagents = sub
    assert a3._llm_plan("查天气") is None

    # 不支持 tools → None
    a4 = SupervisorAgent.__new__(SupervisorAgent)
    a4.router = _FakeRouter(is_stub=True)
    a4._subagents = sub
    assert a4._llm_plan("任务") is None

    os.environ.pop("FOXCLAW_LLM_PLAN", None)
    print("  ✓ 开关 / 多步链路 / 单步回退 / 不支持回退 正确")


def test_replan_enabled_switch():
    print("\n【6】反思重规划开关")
    os.environ.pop("FOXCLAW_LLM_REPLAN", None)
    assert replan_enabled() is False, "默认必须关(可逆)"
    os.environ["FOXCLAW_LLM_REPLAN"] = "1"
    assert replan_enabled() is True
    os.environ.pop("FOXCLAW_LLM_REPLAN", None)
    print("  ✓ 默认关 / '1'开")


def test_llm_replan_parse():
    print("\n【7】llm_replan:修正计划 + 空 steps 放弃 + fail-safe")
    prior = [{"subtask": "查支出", "agent": "finance", "output": "超时", "blocked": True},
             {"subtask": "发邮件", "agent": "office", "output": "未执行", "blocked": False}]
    # 正常:换 agent 重排失败步骤
    r = _FakeRouter(result={"supported": True, "tool_calls": [
        _plan_args("sequential", [
            {"name": "查支出(重试)", "agent": "finance"},
            {"name": "发邮件", "agent": "office"},
        ])]})
    out = llm_replan(r, "查支出再发邮件", prior, _catalog())
    assert out is not None and [s["agent"] for s in out["steps"]] == ["finance", "office"]
    # 空 steps = 模型判定放弃 → 返回 {"steps": []} 而非 None
    r2 = _FakeRouter(result={"supported": True, "tool_calls": [
        _plan_args("sequential", [])]})
    out2 = llm_replan(r2, "任务", prior, _catalog())
    assert out2 is not None and out2["steps"] == []
    # fail-safe:不支持 tools → None
    r3 = _FakeRouter(result={"supported": False})
    assert llm_replan(r3, "任务", prior, _catalog()) is None
    print("  ✓ 修正计划 / 空steps放弃 / 不支持回退 正确")


def test_dispatch_failed():
    print("\n【8】_dispatch_failed:失败判定")
    from foxclaw.agents.agents import _dispatch_failed
    # 多步 blocked → True
    assert _dispatch_failed({"result": {"dispatched_to": "multi", "blocked": True}}) is True
    # parallel steps 含 failed → True
    assert _dispatch_failed({"result": {"dispatched_to": "parallel",
        "parallel_steps": [{"failed": True}]}}) is True
    # 单 Agent / 成功 / 非 dict → False
    assert _dispatch_failed({"result": {"dispatched_to": "finance"}}) is False
    assert _dispatch_failed({"result": {"dispatched_to": "multi", "blocked": False,
        "multi_steps": [{"blocked": False}]}}) is False
    assert _dispatch_failed(None) is False
    print("  ✓ blocked/failed/单Agent/成功/None 判定正确")


def test_replan_and_redo():
    print("\n【9】_replan_and_redo:失败 → 反思 → 重执行 → 成功")
    import asyncio
    from foxclaw.agents.agents import SupervisorAgent
    os.environ["FOXCLAW_LLM_REPLAN"] = "1"
    D = type("D", (), {"AGENT_NAME": "dummy"})
    sub = {"finance": D(), "office": D()}

    # 反思时返回修正计划(重试失败步骤)
    router = _FakeRouter(result={"supported": True, "tool_calls": [
        _plan_args("sequential", [
            {"name": "查支出(重试)", "agent": "finance"},
            {"name": "发邮件", "agent": "office"},
        ])]})
    a = SupervisorAgent.__new__(SupervisorAgent)
    a.router = router
    a._subagents = sub
    a._think = lambda **kw: None   # 屏蔽 thought_chain

    calls = {"n": 0}

    async def fake_dispatch_multi(task, step_routes, *a2, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"result": {"dispatched_to": "multi", "blocked": True,
                               "multi_steps": [{"step": 1, "subtask": "查支出",
                                                "agent": "finance", "output": "超时",
                                                "blocked": True}]}}
        return {"result": {"dispatched_to": "multi", "blocked": False,
                           "multi_steps": [{"step": 1, "subtask": "查支出(重试)",
                                            "agent": "finance", "output": "支出=1000",
                                            "blocked": False},
                                           {"step": 2, "subtask": "发邮件",
                                            "agent": "assistant", "output": "已发送",
                                            "blocked": False}]}}
    a._dispatch_multi = fake_dispatch_multi

    failed_dr = {"result": {"dispatched_to": "multi", "blocked": True,
                            "multi_steps": [{"step": 1, "subtask": "查支出",
                                             "agent": "finance", "output": "超时",
                                             "blocked": True}]}}
    out = asyncio.run(a._replan_and_redo("查支出再发邮件", None, "sess", sub, {}, [], failed_dr))
    assert calls["n"] == 2, f"应重执行 1 次,实际 {calls['n']}"
    assert out["result"]["blocked"] is False
    os.environ.pop("FOXCLAW_LLM_REPLAN", None)
    print("  ✓ 失败触发反思 → 重执行 1 次 → 成功")


def test_replan_gives_up():
    print("\n【10】_replan_and_redo:模型放弃 → 不重执行,保留原结果")
    import asyncio
    from foxclaw.agents.agents import SupervisorAgent
    os.environ["FOXCLAW_LLM_REPLAN"] = "1"
    D = type("D", (), {"AGENT_NAME": "dummy"})
    sub = {"finance": D(), "assistant": D()}

    # 反思返回空 steps(放弃)
    router = _FakeRouter(result={"supported": True, "tool_calls": [
        _plan_args("sequential", [])]})
    a = SupervisorAgent.__new__(SupervisorAgent)
    a.router = router
    a._subagents = sub
    a._think = lambda **kw: None
    called = {"n": 0}

    async def fake_dispatch_multi(task, step_routes, *a2, **kw):
        called["n"] += 1
        return {"result": {"dispatched_to": "multi", "blocked": False}}

    a._dispatch_multi = fake_dispatch_multi
    failed_dr = {"result": {"dispatched_to": "multi", "blocked": True,
                            "multi_steps": [{"step": 1, "subtask": "查支出",
                                             "agent": "finance", "output": "超时",
                                             "blocked": True}]}}
    out = asyncio.run(a._replan_and_redo("查支出", None, "s", sub, {}, [], failed_dr))
    assert called["n"] == 0, "放弃时不应重执行"
    assert out is failed_dr, "放弃时保留原结果"
    os.environ.pop("FOXCLAW_LLM_REPLAN", None)
    print("  ✓ 放弃短路,保留原结果")


def test_sv_replan_stage():
    print("\n【11】sv_replan stage:短路 + 触发")
    import asyncio
    from types import SimpleNamespace
    from foxclaw.pipeline.supervisor_stages import sv_replan

    # 无失败 → 短路
    a1 = SimpleNamespace(_replan_enabled=lambda: True)
    ctx1 = SimpleNamespace(extra={}, agent=a1,
                           dispatch_result={"result": {"dispatched_to": "multi", "blocked": False}})
    asyncio.run(sv_replan(ctx1))
    assert ctx1.dispatch_result["result"]["blocked"] is False

    # 有失败但未开启 → 短路保留原结果
    a2 = SimpleNamespace(_replan_enabled=lambda: False)
    ctx2 = SimpleNamespace(extra={}, agent=a2,
                           dispatch_result={"result": {"dispatched_to": "multi", "blocked": True}})
    asyncio.run(sv_replan(ctx2))
    assert ctx2.dispatch_result["result"]["blocked"] is True

    # 触发路径:有失败 + 开启 → 调用 _replan_and_redo 更新结果
    async def fake_replan(task, sched, session_id, subagents, context, memories, dr, max_rounds=2):
        return {"result": {"dispatched_to": "multi", "blocked": False}}

    a3 = SimpleNamespace(_replan_enabled=lambda: True, _replan_and_redo=fake_replan)
    ctx3 = SimpleNamespace(extra={}, agent=a3,
                           dispatch_result={"result": {"dispatched_to": "multi", "blocked": True}},
                           task="t", sched=None, session_id="s", context={}, memories=[])
    asyncio.run(sv_replan(ctx3))
    assert ctx3.dispatch_result["result"]["blocked"] is False

    print("  ✓ 无失败/未开启短路,触发路径更新结果")


def main():
    print("=" * 60)
    print("LLM 驱动规划器(P0-1)")
    print("=" * 60)
    test_enabled_switch()
    test_build_plan_tool_schema()
    test_llm_plan_failsafe()
    test_llm_plan_parse()
    test_supervisor_llm_plan()
    test_replan_enabled_switch()
    test_llm_replan_parse()
    test_dispatch_failed()
    test_replan_and_redo()
    test_replan_gives_up()
    test_sv_replan_stage()
    print("\n" + "=" * 60)
    print("✓ P0-1 验证完成:LLM 规划 + 反思重规划,opt-in + fail-safe,规则拆解 fallback 保留")
    print("=" * 60)


if __name__ == "__main__":
    main()
