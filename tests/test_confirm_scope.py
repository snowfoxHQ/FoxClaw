"""A3 批准作用域测试 —— (action_class, scope, ttl) 语义 + 原始请求即批准。全离线。

覆盖:
  1. single 作用域:一次性消费 + 参数指纹(既有语义不变)
  2. 用户批准时明确扩大范围(as_scope="session")→ 记录转 session 作用域,不作废
  3. session 作用域:同动作类、参数变了也放行(不校验指纹);跨动作类拒绝;TTL 到期失效
  4. 原始请求即批准:evaluate pre_approved → Stage 4 免确认,reason_code=ORIGIN_APPROVED
  5. pre_approved 不适用于:pay、destructive、deny、污点门槛
  6. _origin_explicitly_requested 条件逐条判定
"""
import os, sys
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).parent.parent))

from types import SimpleNamespace as NS
from foxclaw.core.confirm_nonce import ConfirmationBroker
from foxclaw.skills.permission import evaluate, PermissionRules, ORIGIN_APPROVED
from foxclaw.pipeline.cognitive_pipeline import _origin_explicitly_requested


def _skill(**kw):
    base = dict(id="demo", side_effect="read", destructive=False,
                external=False, read_only=None, trust_level="high")
    base.update(kw)
    return NS(**base)


def test_single_scope_unchanged():
    print("\n【1】single 默认:一次性 + 指纹(既有语义)")
    b = ConfirmationBroker(ttl=60)
    ch = b.issue("s1", "skill:send", {"to": "a"})
    assert ch["scope"] == "single"
    ok, why = b.consume("s1", ch["nonce"], action="skill:send", args={"to": "a"})
    assert ok and why == "ok", (ok, why)
    ok, why = b.consume("s1", ch["nonce"], action="skill:send", args={"to": "a"})
    assert not ok, (ok, why)  # 消费即作废(移除后为 unknown_nonce)
    ch2 = b.issue("s1", "skill:send", {"to": "a"})
    ok, why = b.consume("s1", ch2["nonce"], action="skill:send", args={"to": "b"})
    assert not ok and why == "args_mismatch", (ok, why)
    print("  ✓ 消费即作废、参数漂移拒绝")


def test_session_scope_extend_and_reuse():
    print("\n【2】批准时扩大范围 → session 作用域,同类可复用")
    b = ConfirmationBroker(ttl=60)
    ch = b.issue("s1", "skill:send", {"to": "a"})
    ok, why = b.consume("s1", ch["nonce"], action="skill:send",
                        args={"to": "a"}, as_scope="session")
    assert ok and why == "ok", (ok, why)
    assert b.pending("s1") and b.pending("s1")[0]["scope"] == "session"
    # 同类新参数:放行,不校验指纹
    ok, why = b.consume("s1", ch["nonce"], action="skill:send", args={"to": "b"})
    assert ok, (ok, why)
    print("  ✓ 扩大范围登记生效;同类不同参放行")


def test_session_scope_boundary():
    print("\n【3】session 作用域边界:跨动作类拒绝 / TTL 失效")
    b = ConfirmationBroker(ttl=60)
    ch = b.issue("s1", "skill:send", {"to": "a"}, scope="session")
    ok, why = b.consume("s1", ch["nonce"], action="skill:pay", args={})
    assert not ok and why == "action_mismatch", (ok, why)
    b2 = ConfirmationBroker(ttl=10, clock=lambda: 1000.0)
    ch2 = b2.issue("s1", "skill:send", {"to": "a"}, scope="session", ttl=5)
    ok, why = b2.consume("s1", ch2["nonce"], action="skill:send", args={}, now=1006.0)
    assert not ok and why == "expired", (ok, why)
    print("  ✓ 跨动作类拒绝;TTL 到期即废(作用域不越过有效期)")


def test_pre_approved_waives_stage4():
    print("\n【4】原始请求即批准:Stage 4 免确认,带 ORIGIN_APPROVED 留痕")
    d = evaluate(_skill(side_effect="write"), {}, pre_approved=True)
    assert d.decision == "allow" and d.reason_code == ORIGIN_APPROVED, d
    assert d.tier == "explicit", d
    d = evaluate(_skill(external=True), {}, pre_approved=True)
    assert d.decision == "allow" and d.reason_code == ORIGIN_APPROVED, d
    print("  ✓ write/external 免重复确认,档位仍 explicit")


def test_pre_approved_limits():
    print("\n【5】pre_approved 边界:pay/destructive/deny/污点 不适用")
    d = evaluate(_skill(side_effect="pay"), {}, pre_approved=True)
    assert d.decision == "ask", d
    d = evaluate(_skill(destructive=True), {}, pre_approved=True)
    assert d.decision == "ask", d
    d = evaluate(_skill(), {}, rules=PermissionRules(deny=["demo"]), pre_approved=True)
    assert d.decision == "deny", d
    d = evaluate(_skill(side_effect="send"), {}, taint_level=3, taint_threshold=2,
                 pre_approved=True)
    assert d.decision == "ask" and d.reason_code == "TAINT_CONFIRM", d
    print("  ✓ pay 一律问;destructive 问;deny 优先;污点门槛优先")


def test_origin_explicitly_requested():
    print("\n【6】origin 判定:条件逐条 fail-closed")
    skill = NS(id="skill:send_message", name="发消息", side_effect="send",
               destructive=False)
    ctx = NS(task="帮我发消息给张三", context={"sender_trusted": True})
    assert _origin_explicitly_requested(ctx, skill) is True
    # 派发子任务:不算用户请求
    ctx_d = NS(task="帮我发消息给张三",
               context={"sender_trusted": True, "_no_emit": True})
    assert _origin_explicitly_requested(ctx_d, skill) is False
    # 发送者不可信
    ctx_u = NS(task="帮我发消息给张三", context={"sender_trusted": False})
    assert _origin_explicitly_requested(ctx_u, skill) is False
    # 请求文本没点名动作
    ctx_n = NS(task="帮我处理一下", context={"sender_trusted": True})
    assert _origin_explicitly_requested(ctx_n, skill) is False
    # pay 一律问
    skill_pay = NS(id="skill:pay_bill", name="付款", side_effect="pay",
                   destructive=False)
    ctx_p = NS(task="帮我付款给张三", context={"sender_trusted": True})
    assert _origin_explicitly_requested(ctx_p, skill_pay) is False
    print("  ✓ 派发/不可信/未点名/pay 任一不成立即 False")


def main():
    print("=" * 60)
    print("A3 批准作用域(action_class, scope, ttl)+ 原始请求即批准")
    print("=" * 60)
    test_single_scope_unchanged()
    test_session_scope_extend_and_reuse()
    test_session_scope_boundary()
    test_pre_approved_waives_stage4()
    test_pre_approved_limits()
    test_origin_explicitly_requested()
    print("\n✓ A3 验证完成")


if __name__ == "__main__":
    main()
