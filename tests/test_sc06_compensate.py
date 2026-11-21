"""SC-06·compensate 生产触发链核验与守护。

核验结论:compensate 已真实接线,非 MISSING——
  AHSPipeline.run → compose 失败分支(blocked/error/timeout,非 needs_confirmation)
  → _rollback → 逆序对 side_effect≠read 的已执行步调 compensate;无 compensate 标 manual_required。

此处补追测表缺失的:补偿失败错误处理 / 无补偿手动处理 / 只读跳过 / 逆序 / 端到端审计暴露。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.skills import SkillRegistry, SkillSpec, SkillAdapter
from foxclaw.skills.pipeline import Plan, PlanTask, compose, AHSPipeline


def _plan(steps):
    """手动构造顺序 Plan,绕开 route 打分,确保每个子任务精确路由到指定 skill。"""
    return Plan(intent="", flow_type="sequential",
                tasks=[PlanTask(id=f"T{i}", name=s, suggested_skill=s)
                       for i, s in enumerate(steps, 1)])


def _write_skill(sid, compensate=None, handler=None):
    return SkillSpec(id=sid, name=sid, description=sid, kind="local",
                     side_effect="write",
                     handler=handler or (lambda x="": {"w": x}),
                     params={"x": "str"}, compensate=compensate)


def _fail_skill():
    return SkillSpec(id="fail", name="fail", description="fail", kind="local",
                     side_effect="send", handler=None, params={"to": "str"})


def test_compensate_failure_marks_manual():
    reg = SkillRegistry()

    def boom(out):
        raise RuntimeError("回滚失败")

    reg.register(_write_skill("write_a", compensate=boom))
    reg.register(_fail_skill())
    res = compose(_plan(["write_a", "fail"]), reg, SkillAdapter(reg), confirmed=True)
    assert res.rolled_back
    assert res.rollback_clean is False
    assert "write_a" in res.needs_manual
    assert any(e["status"] == "compensate_failed" for e in res.rollback_log)


def test_side_effect_without_compensate_marks_manual():
    reg = SkillRegistry()
    reg.register(_write_skill("write_a"))
    reg.register(_fail_skill())
    res = compose(_plan(["write_a", "fail"]), reg, SkillAdapter(reg), confirmed=True)
    assert res.rolled_back
    assert res.rollback_clean is False
    assert "write_a" in res.needs_manual
    assert any(e["status"] == "manual_required" for e in res.rollback_log)


def test_read_only_step_not_rolled_back():
    comp_called = []
    reg = SkillRegistry()
    reg.register(SkillSpec(id="recall", name="recall", description="recall",
                           kind="local", side_effect="read",
                           handler=lambda x="": {"r": x}, params={"x": "str"},
                           compensate=lambda out: comp_called.append(out)))
    reg.register(_fail_skill())
    res = compose(_plan(["recall", "fail"]), reg, SkillAdapter(reg), confirmed=True)
    assert res.rolled_back
    assert res.rollback_clean is True
    assert res.needs_manual == []
    assert comp_called == []
    assert all(e.get("skill") != "recall" for e in res.rollback_log)


def test_rollback_reverse_order():
    order = []
    reg = SkillRegistry()
    reg.register(_write_skill("a", compensate=lambda out: order.append("a")))
    reg.register(_write_skill("b", compensate=lambda out: order.append("b")))
    reg.register(_fail_skill())
    res = compose(_plan(["a", "b", "fail"]), reg, SkillAdapter(reg), confirmed=True)
    assert res.rolled_back and res.rollback_clean is True
    assert order == ["b", "a"]


def test_ahs_pipeline_run_rolls_back_and_reports():
    rolled = []
    reg = SkillRegistry()
    reg.register(SkillSpec(id="create_event", name="创建日程", description="创建日程",
                           kind="local", triggers=["创建", "日程"], side_effect="write",
                           handler=lambda title="": {"created": title},
                           params={"title": "str"},
                           compensate=lambda out: rolled.append(out)))
    reg.register(SkillSpec(id="send_email", name="发邮件", description="发邮件",
                           kind="local", triggers=["发送", "邮件"], side_effect="send",
                           handler=None, params={"to": "str"}))
    r = AHSPipeline(reg, SkillAdapter(reg)).run("创建日程 然后 发送邮件", confirmed=True)
    assert not r.success
    assert r.composition["rolled_back"] is True
    assert r.composition["rollback_clean"] is True
    assert len(rolled) == 1
