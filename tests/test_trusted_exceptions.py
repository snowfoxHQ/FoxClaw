"""A2 例外条件清单测试 —— trusted 开闸重构为 AND 条件数组。全离线。

覆盖:
  1. 例外条件逐条单测:任一条件不成立 → 回落主规则(不再放行)
  2. trusted + 例外全条件满足 → 豁免
  3. trusted + 无例外命中 → 落回 Stage 4(该 ask 还 ask)——布尔开闸废除
  4. deny / destructive 在例外之前判定,不可被例外豁免
  5. 无评估上下文 → 条件不成立(fail-closed)
"""
import os, sys
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_TRUSTED_MODE"] = "1"
sys.path.insert(0, str(Path(__file__).parent.parent))

from types import SimpleNamespace as NS
from foxclaw.skills.permission import evaluate, PermissionRules, set_eval_context, clear_eval_context
from foxclaw.skills.exceptions import (
    evaluate_trusted_exception, cond_host_in_test_scope, cond_no_live_markers)


def _skill(**kw):
    base = dict(id="api_call", side_effect="read", destructive=False,
                external=False, read_only=None, trust_level="high")
    base.update(kw)
    return NS(**base)


_USER_CTX = {"source": "user_chat", "untrusted": False}


def test_condition_host_scope():
    print("\n【1】条件1:host 测试域判定")
    ok, why = cond_host_in_test_scope({"url": "http://localhost:8000/api"}, _USER_CTX)
    assert ok, why
    for host in ("http://127.0.0.1:5000", "https://api.staging.example.com",
                 "https://qa.example.com", "https://x.test", "http://[::1]:9"):
        ok, why = cond_host_in_test_scope({"url": host}, _USER_CTX)
        assert ok, f"{host}: {why}"
    ok, why = cond_host_in_test_scope({"url": "https://api.example.com"}, _USER_CTX)
    assert not ok, "生产域不应通过"
    ok, why = cond_host_in_test_scope({"q": "没有 url 的参数"}, _USER_CTX)
    assert not ok, "找不到 host 应 fail-closed"
    print("  ✓ localhost/127/::1/*.test/staging 标记通过;生产域与无 host 拒绝")


def test_condition_live_markers():
    print("\n【2】条件2:live/production 标记")
    ok, why = cond_no_live_markers({"key": "test-key-123"}, _USER_CTX)
    assert ok, why
    ok, why = cond_no_live_markers({"production_key": "x"}, _USER_CTX)
    assert not ok, "键标记 production 应拒绝"
    ok, why = cond_no_live_markers({"note": "this is the prod key"}, _USER_CTX)
    assert not ok, "值含 prod 标记应拒绝"
    print("  ✓ live/production 键值均拦截")


def test_exception_all_conditions():
    print("\n【3】例外引擎:全条件满足才豁免")
    ok, why = evaluate_trusted_exception(
        {"url": "http://localhost:8000", "key": "test-abc"}, _USER_CTX)
    assert ok, why
    # 条件3不成立:无上下文
    ok, why = evaluate_trusted_exception(
        {"url": "http://localhost:8000", "key": "test-abc"}, {})
    assert not ok, "无来源上下文应拒绝"
    # 条件1不成立:生产域
    ok, why = evaluate_trusted_exception(
        {"url": "https://api.example.com", "key": "test-abc"}, _USER_CTX)
    assert not ok, "生产域应拒绝"
    print(f"  ✓ 全条件通过豁免;任一不成立回落({why[:40]}…)")


def test_trusted_no_longer_blanket():
    print("\n【4】trusted 不再布尔放行:无例外命中 → 落回 Stage 4")
    tok = set_eval_context(_USER_CTX)
    try:
        # 生产域 + external:trusted 下不再无条件放行,落回 ask
        d = evaluate(_skill(external=True, side_effect="read"),
                     {"url": "https://api.example.com"}, mode="trusted")
        assert d.decision == "ask", d
        # 例外命中:localhost + 测试 key → 豁免
        d = evaluate(_skill(external=True, side_effect="read"),
                     {"url": "http://localhost:8000", "key": "test-abc"},
                     mode="trusted")
        assert d.decision == "allow" and "例外" in d.reason, d
    finally:
        clear_eval_context(tok)
    print("  ✓ 布尔开闸废除:trusted 无例外 = 与 strict 同口径")


def test_deny_and_destructive_immune():
    print("\n【5】deny / destructive 不可被例外豁免")
    tok = set_eval_context(_USER_CTX)
    try:
        d = evaluate(_skill(), {"url": "http://localhost:8000", "key": "t"},
                     rules=PermissionRules(deny=["api_call"]), mode="trusted")
        assert d.decision == "deny" and d.tier == "prohibited", d
        d = evaluate(_skill(destructive=True, side_effect="write"),
                     {"url": "http://localhost:8000", "key": "t"}, mode="trusted")
        assert d.decision == "ask", "destructive 必须仍走确认"
    finally:
        clear_eval_context(tok)
    print("  ✓ deny 与 destructive 确认在例外之前,例外豁免不了")


def test_trusted_mode_disabled():
    print("\n【6】trusted 未显式授权 → 降级 strict,例外引擎不参与")
    os.environ["FOXCLAW_TRUSTED_MODE"] = "0"
    tok = set_eval_context(_USER_CTX)
    try:
        d = evaluate(_skill(external=True, side_effect="read"),
                     {"url": "http://localhost:8000", "key": "test-abc"},
                     mode="trusted")
        assert d.decision == "ask", "未授权的 trusted 调用一律降级"
    finally:
        os.environ["FOXCLAW_TRUSTED_MODE"] = "1"
        clear_eval_context(tok)
    print("  ✓ env 未开闸时 mode=trusted 降级 strict(既有守卫不变)")


def main():
    print("=" * 60)
    print("A2 例外条件清单(trusted 开闸重构)")
    print("=" * 60)
    test_condition_host_scope()
    test_condition_live_markers()
    test_exception_all_conditions()
    test_trusted_no_longer_blanket()
    test_deny_and_destructive_immune()
    test_trusted_mode_disabled()
    print("\n✓ A2 验证完成")


if __name__ == "__main__":
    main()
