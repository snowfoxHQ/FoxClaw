"""关键操作(send/pay/destructive)的会话 nonce 二次确认。

锁住:确认码绑定「会话 + 动作 + 参数」、单次使用、过期即废;无效码不会降级成放行
(退回 needs_confirmation 并换发新码);只读技能与未注入 broker 的路径行为不变。
"""

from __future__ import annotations

import pytest

from foxclaw.core.confirm_nonce import (
    ConfirmationBroker,
    action_fingerprint,
    is_critical,
)
from foxclaw.skills import (
    SkillAdapter,
    SkillRegistry,
    SkillSpec,
    reset_loop_session,
    set_loop_session,
)
from foxclaw.skills import pipeline as sk_pipeline

SESSION = "sess-1"


def test_issue_consume_single_use() -> None:
    b = ConfirmationBroker(ttl=300, clock=lambda: 1000.0)
    ch = b.issue(SESSION, "skill:mail_send", {"to": "a@x"})
    assert ch["nonce"] and ch["expires_at"] == 1300.0
    assert b.consume(SESSION, ch["nonce"], "skill:mail_send", {"to": "a@x"},
                     now=1000.0) == (True, "ok")
    assert b.consume(SESSION, ch["nonce"], "skill:mail_send", {"to": "a@x"},
                     now=1000.0) == (False, "unknown_nonce")


def test_expired_nonce_rejected() -> None:
    b = ConfirmationBroker(ttl=60, clock=lambda: 1000.0)
    ch = b.issue(SESSION, "skill:pay", {"amount": 1})
    assert b.consume(SESSION, ch["nonce"], "skill:pay", {"amount": 1},
                     now=1061.0) == (False, "expired")


def test_args_and_action_drift_rejected() -> None:
    b = ConfirmationBroker(clock=lambda: 1000.0)
    ch = b.issue(SESSION, "skill:mail_send", {"to": "a@x"})
    assert b.consume(SESSION, ch["nonce"], "skill:mail_send", {"to": "b@x"},
                     now=1000.0) == (False, "args_mismatch")
    assert b.consume(SESSION, ch["nonce"], "skill:pay", {"to": "a@x"},
                     now=1000.0) == (False, "action_mismatch")
    assert b.consume(SESSION, ch["nonce"], "skill:mail_send", {"to": "a@x"},
                     now=1000.0) == (True, "ok")


def test_session_mismatch_and_missing_nonce() -> None:
    b = ConfirmationBroker(clock=lambda: 1000.0)
    ch = b.issue(SESSION, "skill:pay", {"amount": 1})
    assert b.consume("other", ch["nonce"], "skill:pay", {"amount": 1},
                     now=1000.0) == (False, "session_mismatch")
    assert b.consume(SESSION, "", "skill:pay", {"amount": 1}, now=1000.0) == (False, "missing_nonce")
    assert b.consume(SESSION, "deadbeef", "skill:pay", {"amount": 1},
                     now=1000.0) == (False, "unknown_nonce")


def test_fingerprint_is_arg_order_insensitive_and_critical_classification() -> None:
    assert action_fingerprint("a", {"x": 1, "y": 2}) == action_fingerprint("a", {"y": 2, "x": 1})
    assert is_critical("send") and is_critical("pay") and is_critical("read", destructive=True)
    assert not is_critical("read")


def test_pending_is_bounded() -> None:
    b = ConfirmationBroker(max_pending=8, clock=lambda: 1000.0)
    for _ in range(20):
        b.issue(SESSION, "skill:pay", {"amount": 1})
    assert len(b) == 8, "签到表要有界,不能当内存放大器"
    assert len(b.pending(SESSION)) == 8
    assert b.pending("nobody") == []


class _Calls:
    def __init__(self):
        self.n = 0

    def __call__(self, **kw):
        self.n += 1
        return {"sent": True}


def _build(broker):
    calls = _Calls()
    reg = SkillRegistry()
    reg.register(SkillSpec(id="mail_send", name="发邮件", description="发送邮件",
                           triggers=["发邮件"], side_effect="send", handler=calls))
    reg.register(SkillSpec(id="calc", name="计算", description="算数",
                           triggers=["算"], side_effect="read",
                           handler=lambda **kw: {"r": 1}))
    return SkillAdapter(reg, confirms=broker), reg, calls


def test_send_requires_nonce_even_when_confirmed():
    broker = ConfirmationBroker()
    adapter, reg, calls = _build(broker)
    tok = set_loop_session(SESSION)
    try:
        skill = reg.get("mail_send")
        args = {"to": "a@x", "body": "hi"}

        first = adapter.invoke(skill, args, confirmed=False)
        assert first.status == "needs_confirmation"
        assert first.confirm_nonce and first.confirm_expires_at > 0
        assert calls.n == 0

        no_nonce = adapter.invoke(skill, args, confirmed=True)
        assert no_nonce.status == "needs_confirmation" and calls.n == 0
        assert "missing_nonce" in no_nonce.message
        assert no_nonce.confirm_nonce and no_nonce.confirm_nonce != first.confirm_nonce

        ok = adapter.invoke(skill, args, confirmed=True,
                            confirm_nonce=no_nonce.confirm_nonce)
        assert ok.success and calls.n == 1

        replay = adapter.invoke(skill, args, confirmed=True,
                                confirm_nonce=no_nonce.confirm_nonce)
        assert replay.status == "needs_confirmation" and calls.n == 1
        assert "unknown_nonce" in replay.message
    finally:
        reset_loop_session(tok)


def test_nonce_bound_to_args():
    broker = ConfirmationBroker()
    adapter, reg, calls = _build(broker)
    tok = set_loop_session(SESSION)
    try:
        skill = reg.get("mail_send")
        ch = adapter.invoke(skill, {"to": "a@x"}, confirmed=False)
        drift = adapter.invoke(skill, {"to": "b@x"}, confirmed=True,
                               confirm_nonce=ch.confirm_nonce)
        assert drift.status == "needs_confirmation" and calls.n == 0
        assert "args_mismatch" in drift.message
    finally:
        reset_loop_session(tok)


def test_read_skill_and_no_broker_unaffected():
    adapter, reg, calls = _build(ConfirmationBroker())
    assert adapter.invoke(reg.get("calc"), {}, confirmed=True).success

    plain = SkillAdapter(reg)
    assert plain.invoke(reg.get("mail_send"), {"to": "a@x"}, confirmed=True).success
    assert calls.n >= 1


def test_pipeline_invoke_tolerates_old_adapter():
    """测试/第三方 adapter 只实现 4 参 invoke 时自动降级(签名探测,不吞内部 TypeError)。"""
    seen = {}

    class _Old:
        def invoke(self, skill, args, confirmed=False, mode="strict"):
            seen.update({"skill": skill.id, "confirmed": confirmed})
            return "out"

    out = sk_pipeline._invoke(_Old(), SkillSpec(id="s", name="n", description="d"),
                              {"a": 1}, True, "nonce")
    assert out == "out" and seen == {"skill": "s", "confirmed": True}


def test_pipeline_passes_nonce_through(monkeypatch):
    got = {}

    class _New:
        def invoke(self, skill, args, confirmed=False, mode="strict", confirm_nonce=""):
            got["nonce"] = confirm_nonce
            return "out"

    sk_pipeline._invoke(_New(), SkillSpec(id="s", name="n", description="d"),
                        {}, True, "abc")
    assert got["nonce"] == "abc"


@pytest.mark.parametrize("bad", ["", "  "])
def test_empty_nonce_never_authorizes_broker(bad):
    b = ConfirmationBroker(clock=lambda: 1000.0)
    b.issue(SESSION, "skill:pay", {"amount": 1})
    assert b.consume(SESSION, bad, "skill:pay", {"amount": 1}, now=1000.0)[0] is False


# ── 确认闭环接线回归:nonce 必须能从「签发 → 用户 → 重跑」走通 ──────────────

def test_skillbox_use_skill_passes_nonce():
    """【接线回归】use_skill(confirmed=True, confirm_nonce=...) → critical skill 真正执行。
    此前 use_skill 没有传 nonce 的通道,Agent 路径上确认后重跑永远 needs_confirmation。"""
    from foxclaw.agents.mixins.skillbox import SkillBoxMixin

    broker = ConfirmationBroker()
    adapter, reg, calls = _build(broker)

    class _Agent(SkillBoxMixin):
        skill_registry = reg
        skill_adapter = adapter
        _session_id = SESSION
        _taint = 0
        _untrusted_source = False

        def _think(self, **kw):
            return None

    tok = set_loop_session(SESSION)
    try:
        skill = reg.get("mail_send")
        first = adapter.invoke(skill, {"to": "a@x"}, confirmed=False)
        assert first.status == "needs_confirmation"

        out = _Agent().use_skill("发邮件", {"to": "a@x"},
                                 confirmed=True, confirm_nonce=first.confirm_nonce)
        assert out["result"]["status"] == "done", out
        assert calls.n == 1
    finally:
        reset_loop_session(tok)


def test_skillbox_use_skill_without_nonce_stays_pending():
    """【安全回归】use_skill 只有 confirmed=True 而无有效 nonce → 仍 needs_confirmation,
    不执行(新签名不会把空 nonce 变成放行)。"""
    from foxclaw.agents.mixins.skillbox import SkillBoxMixin

    broker = ConfirmationBroker()
    adapter, reg, calls = _build(broker)

    class _Agent(SkillBoxMixin):
        skill_registry = reg
        skill_adapter = adapter
        _session_id = SESSION
        _taint = 0
        _untrusted_source = False

        def _think(self, **kw):
            return None

    tok = set_loop_session(SESSION)
    try:
        out = _Agent().use_skill("发邮件", {"to": "a@x"}, confirmed=True)
        assert out["result"]["status"] == "needs_confirmation", out
        assert calls.n == 0
    finally:
        reset_loop_session(tok)


def test_cognitive_pipeline_dispatch_passes_context_nonce():
    """【接线回归】_act_via_skill 把 context['_confirm_nonce'] 透传给 adapter;
    待确认结果把 confirm_nonce 带回给调用方(pending_action 消费)。"""
    import asyncio
    import types

    from foxclaw.pipeline.cognitive_pipeline import _act_via_skill

    broker = ConfirmationBroker()
    adapter, reg, calls = _build(broker)
    skill = reg.get("mail_send")

    tok = set_loop_session("sess-pipe")
    try:
        # dispatch 走 _build_args(skill, task, {}) → mail_send 未声明 params,实参为 {};
        # nonce 必须与 dispatch 时的同一参数指纹绑定,故用 {} 签发。
        first = adapter.invoke(skill, {}, confirmed=False)
        assert first.status == "needs_confirmation"

        ctx = types.SimpleNamespace(
            agent=types.SimpleNamespace(skill_registry=reg, skill_adapter=adapter),
            route={"target": "skill", "skill_id": "mail_send"},
            task="发邮件", context={"_confirmed": "mail_send",
                                    "_confirm_nonce": first.confirm_nonce},
            session_id="sess-pipe")
        result, extra = asyncio.run(_act_via_skill(ctx))
        assert result["status"] == "done" and calls.n == 1, (result, extra)

        # 第二次:未确认 → needs_confirmation,结果必须带出新 nonce(供 pending_action 回传)
        ctx.context = {}
        result, extra = asyncio.run(_act_via_skill(ctx))
        assert result["status"] == "needs_confirmation"
        assert result["confirm_nonce"] and calls.n == 1
    finally:
        reset_loop_session(tok)


def test_cognitive_pipeline_dispatch_raises_taint(monkeypatch):
    """【接线回归】dispatch 透传会话污点:污点达标时 Stage 3.5 先于 Stage 4 命中,
    理由为「污点防护」;无污点时是普通「write 副作用」需确认。
    这证明 taint 真的从 agent 流到了权限管线,而不是 dispatch 路径永远 0。"""
    import asyncio
    import types

    from foxclaw.core import injection_guard as IG
    from foxclaw.pipeline.cognitive_pipeline import _act_via_skill

    reg = SkillRegistry()
    reg.register(SkillSpec(id="write_note", name="写笔记", description="写笔记",
                           triggers=["写"], side_effect="write",
                           handler=lambda **kw: {"written": True},
                           params={"text": "str"}))
    adapter = SkillAdapter(reg, confirms=ConfirmationBroker())

    def _ctx(taint: int):
        return types.SimpleNamespace(
            agent=types.SimpleNamespace(skill_registry=reg, skill_adapter=adapter,
                                        _taint_level=taint),
            route={"target": "skill", "skill_id": "write_note"},
            task="写笔记 hello", context={}, session_id="sess-taint")

    tok = set_loop_session("sess-taint")
    try:
        tainted, _ = asyncio.run(_act_via_skill(_ctx(IG.TAINT_PERSONAL)))
        assert tainted["status"] == "needs_confirmation" and "污点" in tainted["message"], tainted

        clean, _ = asyncio.run(_act_via_skill(_ctx(0)))
        assert clean["status"] == "needs_confirmation"
        assert "污点" not in clean["message"] and "write 副作用" in clean["message"], clean
    finally:
        reset_loop_session(tok)
