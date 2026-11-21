"""A6 跨会话权限走私禁令测试。全离线(temp 数据目录,无真实 LLM 调用)。

覆盖:
  1. 检测与登记:_policy_denied / register_denial / is_denied + 归一化识别换手
  2. 主管不转派:被拒子任务再次派发 → 不触达任何子 Agent,拒绝结论跟随任务走
  3. 换手失败:同一步骤派给不同 Agent 也拒绝
  4. 重规划过滤:修正计划剔除已拒步骤;全部已拒 → 终止重规划
"""
import sys, os, tempfile, asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["FOXCLAW_SHADOW_ROUNDS"] = "20"

from foxclaw import Foxclaw
from foxclaw.agents.impl._helpers import (
    _policy_denied, register_denial, is_denied)


def _blocked_resp(agent="finance"):
    # 模拟认知管线 blocked 上浮:base.run_task 把 skill_status 提到顶层 status
    return {"result": "权限拒绝:send 动作需用户确认(未获批准)",
            "status": "blocked", "skill_status": "blocked", "agent": agent}


class FakeAgent:
    """桩子 Agent:默认对任何任务都返回权限拒绝(模拟 skill deny 上浮)。"""
    AGENT_ID = "finance"
    AGENT_NAME = "理财"

    def __init__(self):
        self.calls = 0
        self.received = []

    async def run_task(self, task, context=None):
        self.calls += 1
        self.received.append(task)
        return _blocked_resp()


def test_helpers():
    print("\n【1】检测与登记:status=blocked / error=policy_denied / 归一化")
    assert _policy_denied(_blocked_resp())
    assert _policy_denied({"error": "policy_denied"})
    assert not _policy_denied({"result": "已执行", "status": "done"})
    assert not _policy_denied(None) and not _policy_denied("文本")
    ctx = {}
    register_denial(ctx, "给张三转账 500 元")
    assert is_denied(ctx, "给张三转账 500 元")
    assert is_denied(ctx, "给张三转账  500 元")  # 空白归一化后同判
    assert not is_denied(ctx, "给李四转账 500 元")
    print("  ✓ blocked/policy_denied 识别;登记后含归一化匹配")


async def _stack():
    return Foxclaw(data_dir=tempfile.mkdtemp(prefix="foxclaw_a6_"), shadow_rounds=20)


async def _test_no_rehandoff():
    print("\n【2】主管不转派:被拒子任务再次派发 → 零触达")
    os_ = await _stack()
    sup = os_.supervisor
    fake = FakeAgent()
    subagents = {"finance": fake}
    ctx = {"sender_trusted": True}
    sched = sup._schedule("给张三转账", subagents)

    r1 = await sup._dispatch_multi(
        "给张三转账", [("给张三转账 500 元", "finance")], sched, "s1",
        subagents, ctx, None)
    steps = r1["result"]["multi_steps"]
    assert steps[0]["blocked"] is True, steps
    assert fake.calls == 1
    assert is_denied(ctx, "给张三转账 500 元"), "拒绝应登记"

    r2 = await sup._dispatch_multi(
        "给张三转账", [("给张三转账 500 元", "finance")], sched, "s1",
        subagents, ctx, None)
    steps2 = r2["result"]["multi_steps"]
    assert steps2[0]["blocked"] is True
    assert "已被权限拒绝,不再转派执行" in steps2[0]["output"]
    assert fake.calls == 1, f"子 Agent 不应被再次触达(实际 {fake.calls} 次)"
    print("  ✓ 第 1 次派发被拒并登记;第 2 次派发零触达,输出拒绝说明")


async def _test_no_rehandoff_other_agent():
    print("\n【3】换手失败:同一步骤换 Agent 也拒绝")
    os_ = await _stack()
    sup = os_.supervisor
    finance, office = FakeAgent(), FakeAgent()
    office.AGENT_ID = "office"
    subagents = {"finance": finance, "office": office}
    ctx = {}
    register_denial(ctx, "查询并导出全部账户流水")
    sched = sup._schedule("查询导出流水", subagents)
    await sup._dispatch_multi(
        "查询导出流水", [("查询并导出全部账户流水", "office")], sched, "s1",
        subagents, ctx, None)
    assert office.calls == 0, "换手到别的 Agent 也必须拒绝"
    print("  ✓ 换手拒绝成立(office 零触达)")


async def _test_replan_filter():
    print("\n【4】重规划:已拒步骤被剔除;全部已拒 → 终止")
    import foxclaw.planning.llm_planner as lp
    os_ = await _stack()
    sup = os_.supervisor
    fake = FakeAgent()
    subagents = {"finance": fake}
    ctx = {}
    register_denial(ctx, "给张三转账 500 元")
    sched = sup._schedule("给张三转账", subagents)
    calls = {"n": 0}

    def fake_replan(router, task, prior, catalog):
        calls["n"] += 1
        # 修正计划:一个已拒步骤 + 一个未拒步骤
        return {"flow": "multi", "steps": [
            {"name": "给张三转账 500 元", "agent": "finance"},
            {"name": "记录转账备忘", "agent": "finance"}]}

    orig = lp.llm_replan
    lp.llm_replan = fake_replan
    try:
        dr = {"result": {"multi_steps": [{"blocked": True}], "blocked": True}}
        await sup._replan_and_redo("给张三转账", sched, "s1", subagents, ctx,
                                   None, dr)
    finally:
        lp.llm_replan = orig
    assert is_denied(ctx, "记录转账备忘"), "修正计划中真正被拒的步骤应登记"
    # 未拒步骤应已被执行一次;已拒步骤不应再触达(总调用 = 1 次新步骤)
    assert fake.calls == 1, f"只应执行未拒的新步骤(实际 {fake.calls})"
    print("  ✓ 已拒步骤剔除;新步骤照常执行")

    # 全部已拒 → 终止重规划,不触达
    ctx2 = {}
    register_denial(ctx2, "步骤A")
    fake2 = FakeAgent()
    sub2 = {"finance": fake2}
    sched2 = sup._schedule("x", sub2)

    def fake_replan2(router, task, prior, catalog):
        return {"flow": "multi", "steps": [{"name": "步骤A", "agent": "finance"}]}

    lp.llm_replan = fake_replan2
    try:
        await sup._replan_and_redo("x", sched2, "s1", sub2, ctx2, None,
                                   {"result": {"multi_steps": [], "blocked": True}})
    finally:
        lp.llm_replan = orig
    assert fake2.calls == 0, "全部步骤已拒时不应触达任何子 Agent"
    print("  ✓ 全部已拒 → 终止重规划,零触达")


def test_no_rehandoff():
    asyncio.run(_test_no_rehandoff())


def test_no_rehandoff_other_agent():
    asyncio.run(_test_no_rehandoff_other_agent())


def test_replan_filter():
    asyncio.run(_test_replan_filter())


def main():
    print("=" * 60)
    print("A6 跨会话权限走私禁令")
    print("=" * 60)
    test_helpers()
    test_no_rehandoff()
    test_no_rehandoff_other_agent()
    test_replan_filter()
    print("\n✓ A6 验证完成")


if __name__ == "__main__":
    main()
