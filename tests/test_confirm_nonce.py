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


# ── broker 单元 ────────────────────────────────────────────

def test_issue_consume_single_use() -> None:
    b = ConfirmationBroker(ttl=300, clock=lambda: 1000.0)
    ch = b.issue(SESSION, "skill:mail_send", {"to": "a@x"})
    assert ch["nonce"] and ch["expires_at"] == 1300.0
    assert b.consume(SESSION, ch["nonce"], "skill:mail_send", {"to": "a@x"},
                     now=1000.0) == (True, "ok")
    # 第二次:已消费 → 拒绝(单次使用)
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
    # 确认"发给 A"却执行"发给 B" → 指纹不匹配
    assert b.consume(SESSION, ch["nonce"], "skill:mail_send", {"to": "b@x"},
                     now=1000.0) == (False, "args_mismatch")
    assert b.consume(SESSION, ch["nonce"], "skill:pay", {"to": "a@x"},
                     now=1000.0) == (False, "action_mismatch")
    # 原动作原参数仍可用(前两次失败没有消费掉它)
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


# ── adapter 集成 ───────────────────────────────────────────

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

        # 只有 confirmed=True 没有确认码 → 不放行(退回待确认,换发新码)
        no_nonce = adapter.invoke(skill, args, confirmed=True)
        assert no_nonce.status == "needs_confirmation" and calls.n == 0
        assert "missing_nonce" in no_nonce.message
        assert no_nonce.confirm_nonce and no_nonce.confirm_nonce != first.confirm_nonce

        ok = adapter.invoke(skill, args, confirmed=True,
                            confirm_nonce=no_nonce.confirm_nonce)
        assert ok.success and calls.n == 1

        # 重放同一个码 → 拒绝,不执行
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

    plain = SkillAdapter(reg)                      # 未注入 broker → 旧语义
    assert plain.invoke(reg.get("mail_send"), {"to": "a@x"}, confirmed=True).success
    assert calls.n >= 1


# ── pipeline 兼容 ──────────────────────────────────────────

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
