"""C1 无人值守协议测试 —— 会话活跃信号 + pending 的 attended/parked 标记。全离线。

覆盖:
  1. PresenceTracker:touch 后窗口内 attended;超窗/无记录 False(fail-closed)
  2. 窗口配置:env FOXCLAW_ATTENDED_WINDOW 与非法值兜底
  3. 渠道消息到达即 touch:handle 一条消息后 attended=True
  4. pending 携带 attended/parked;无人值守下 parked=True
  5. behavior 规则加载:unattended_protocol 可解析、可注入
"""
import os, sys, asyncio
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.core.presence import PresenceTracker, default_window, touch, attended


def test_tracker_windowing():
    print("\n【1】活跃信号:窗口内 True,超窗/无记录 False")
    t = PresenceTracker()
    assert t.attended("s1") is False, "无记录 fail-closed"
    t.touch("s1", now=1000.0)
    assert t.attended("s1", window=300, now=1100.0) is True
    assert t.attended("s1", window=300, now=1301.0) is False, "超窗"
    assert t.attended("unknown", window=300) is False
    assert t.attended("") is False
    t.forget("s1")
    assert t.attended("s1", window=300, now=1001.0) is False
    print("  ✓ touch/超窗/无记录/forget 全部符合;空会话键拒绝")


def test_window_config():
    print("\n【2】窗口配置:env 读取与非法值兜底")
    old = os.environ.get("FOXCLAW_ATTENDED_WINDOW")
    try:
        os.environ["FOXCLAW_ATTENDED_WINDOW"] = "120"
        assert default_window() == 120.0
        os.environ["FOXCLAW_ATTENDED_WINDOW"] = "abc"
        assert default_window() == 300.0, "非法值兜底默认"
        os.environ["FOXCLAW_ATTENDED_WINDOW"] = "-5"
        assert default_window() == 300.0, "非正数兜底默认"
    finally:
        if old is None:
            os.environ.pop("FOXCLAW_ATTENDED_WINDOW", None)
        else:
            os.environ["FOXCLAW_ATTENDED_WINDOW"] = old
    print("  ✓ env 正常读取;abc/负数兜底 300s")


async def _test_channel_touch():
    from foxclaw.channels.dispatcher import ChannelDispatcher
    from foxclaw.channels import InboundMessage

    dispatched = {}

    class FakeSup:
        async def run_task(self, text, context=None):
            dispatched["session_id"] = (context or {}).get("session_id")
            dispatched["attended_ctx"] = (context or {}).get("attended")
            return {"result": "好的,已完成", "status": "done"}

    d = ChannelDispatcher(FakeSup())
    msg = InboundMessage(channel="test", message_id="m1", text="帮我查一下今天的日程",
                         sender_id="u1", sender_name="用户")
    await d.handle(msg)
    sid = dispatched["session_id"]
    assert sid and attended(sid) is True, "渠道消息到达应已 touch"
    assert dispatched["attended_ctx"] is True
    # 超窗后无人值守
    assert attended(sid, window=0.0) is False
    print("  ✓ 消息到达即 touch,context.attended=True;超窗判定无人值守")


def test_channel_touch():
    asyncio.run(_test_channel_touch())


def test_pending_attended_fields():
    print("\n【4】pending 携带 attended/parked:无人值守下 parked=True")
    from foxclaw.pipeline.cognitive_pipeline import _act_via_skill, PipelineContext
    from foxclaw.skills import SkillAdapter, SkillRegistry, SkillSpec

    class FakeAgent:
        AGENT_ID = "test"
        skill_registry = SkillRegistry()
        skill_adapter = SkillAdapter(skill_registry)
        _untrusted = False

    fake = FakeAgent()
    fake.skill_registry.register(SkillSpec(
        id="skill:send_report", name="发报告", description="发送报告",
        kind="local", side_effect="send",
        handler=lambda **kw: {"sent": True}, params={"to": "str"}))
    ctx = PipelineContext(task="把报告发出去", agent=fake, session_id="sess-u1",
                          context={"sender_trusted": True,
                                   "route": {"target": "skill",
                                             "skill_id": "skill:send_report"}})
    ctx.route = {"target": "skill", "skill_id": "skill:send_report"}
    # 有人看:刚 touch 过
    touch("sess-u1")
    r = asyncio.run(_act_via_skill(ctx))
    result = r[0]
    assert result["status"] == "needs_confirmation" and result["pending"] is True
    assert result["attended"] is True and result["parked"] is False, result
    # 无人值守:清掉在场记录后走同一路径(此前 touch 立即超窗)
    from foxclaw.core import presence as _presence
    _presence.forget("sess-u1")
    r3 = asyncio.run(_act_via_skill(ctx))
    result3 = r3[0]
    assert result3["attended"] is False and result3["parked"] is True, result3
    print("  ✓ 在场 → parked=False;无人 → parked=True(摆出决策+停的信号)")


def test_behavior_rule():
    print("\n【5】behavior 规则:unattended_protocol 加载/注入")
    from foxclaw.behavior.spec import load, prompt_block
    spec = load()
    r = spec.by_id("unattended_protocol")
    assert r is not None and r.injects, r
    assert "无人值守" in r.rule
    block = prompt_block(spec)
    assert "unattended_protocol" in block
    print("  ✓ 规则登记、prompt 注入通过")


def main():
    print("=" * 60)
    print("C1 无人值守协议(会话活跃信号)")
    print("=" * 60)
    test_tracker_windowing()
    test_window_config()
    test_channel_touch()
    test_pending_attended_fields()
    test_behavior_rule()
    print("\n✓ C1 验证完成")


if __name__ == "__main__":
    main()
