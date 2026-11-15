"""A1 动作三分类测试 —— tier 标注命中规则所属的档,不随裁决结果漂移。全离线。

覆盖:
  1. deny → prohibited;readonly 模式 deny → prohibited
  2. destructive / 副作用 / ask 规则 → explicit;确认后放行 tier 仍是 explicit
  3. 只读 / allow 规则 → regular
  4. 参数缺失(ask 但非档位判定)→ tier 为空
  5. PolicyGate 门面透传 tier;fail-closed 路径 tier 为空
"""
import os, sys
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).parent.parent))

from types import SimpleNamespace as NS
from foxclaw.skills.permission import (
    evaluate, PermissionRules, TIER_PROHIBITED, TIER_EXPLICIT, TIER_REGULAR)
from foxclaw.kernel import PolicyGate


def _skill(**kw):
    base = dict(id="demo", side_effect="read", destructive=False,
                external=False, read_only=None, trust_level="high")
    base.update(kw)
    return NS(**base)


def test_deny_is_prohibited():
    print("\n【1】deny → prohibited(即使 confirmed / trusted 也不变)")
    d = evaluate(_skill(), {}, rules=PermissionRules(deny=["demo"]), confirmed=True)
    assert d.decision == "deny" and d.tier == TIER_PROHIBITED, d
    d = evaluate(_skill(), {}, rules=PermissionRules(deny=["demo"]), mode="trusted")
    assert d.tier == TIER_PROHIBITED, d
    d = evaluate(_skill(side_effect="write"), {}, mode="readonly")
    assert d.decision == "deny" and d.tier == TIER_PROHIBITED, d
    print("  ✓ deny 路径 tier=prohibited,不受 confirmed/trusted 影响")


def test_explicit_tier_stays_explicit_after_confirm():
    print("\n【2】显式批准档:ask 与确认后放行都是 explicit(档随规则走)")
    d = evaluate(_skill(side_effect="write"), {})
    assert d.decision == "ask" and d.tier == TIER_EXPLICIT, d
    d = evaluate(_skill(side_effect="write"), {}, confirmed=True)
    assert d.decision == "allow" and d.tier == TIER_EXPLICIT, d
    d = evaluate(_skill(destructive=True), {})
    assert d.decision == "ask" and d.tier == TIER_EXPLICIT, d
    d = evaluate(_skill(destructive=True), {}, confirmed=True)
    assert d.decision == "allow" and d.tier == TIER_EXPLICIT, d
    d = evaluate(_skill(), {}, rules=PermissionRules(ask=["demo"]), confirmed=True)
    assert d.decision == "allow" and d.tier == TIER_EXPLICIT, d
    d = evaluate(_skill(external=True), {}, confirmed=True)
    assert d.decision == "allow" and d.tier == TIER_EXPLICIT, d
    print("  ✓ 确认后放行 tier 仍标 explicit,审计可查「这是需批准类动作」")


def test_regular_tier():
    print("\n【3】常规档:只读 / allow 规则命中,无确认触发")
    d = evaluate(_skill(), {})
    assert d.decision == "allow" and d.tier == TIER_REGULAR, d
    d = evaluate(_skill(), {}, rules=PermissionRules(allow=["demo"]))
    assert d.decision == "allow" and d.tier == TIER_REGULAR, d
    print("  ✓ 未列出的无副作用动作 = regular")


def test_missing_params_tier_empty():
    print("\n【4】参数缺失 ask 属参数校验,不落档")
    d = evaluate(_skill(), {}, rules=PermissionRules())
    # 无 params 属性 → 无缺参;给带 required params 的 skill
    sk = NS(id="demo", side_effect="read", destructive=False, external=False,
            read_only=True, trust_level="high",
            params={"to": {"required": True}})
    d = evaluate(sk, {})
    assert d.decision == "ask" and d.tier == "", d
    print("  ✓ MISSING_PARAMS 的 ask 不标档位")


def test_gate_passthrough_and_fail_closed():
    print("\n【5】PolicyGate 透传 tier;fail-closed 路径 tier 为空")
    gate = PolicyGate(action_evaluator=lambda *a, **k: evaluate(*a, **k))
    v = gate.check_action(_skill(side_effect="write"), {})
    assert v.level == "confirm" and v.tier == TIER_EXPLICIT, v
    v = gate.check_action(_skill(), {}, rules=PermissionRules(deny=["demo"]))
    assert v.level == "deny" and v.tier == TIER_PROHIBITED, v
    v = PolicyGate().check_action(_skill(), {})
    assert v.level == "deny" and v.tier == "", v
    print("  ✓ 门面 tier 透传 + 无评估器 fail-closed 不标档")


def main():
    print("=" * 60)
    print("A1 动作三分类(prohibited / explicit / regular)")
    print("=" * 60)
    test_deny_is_prohibited()
    test_explicit_tier_stays_explicit_after_confirm()
    test_regular_tier()
    test_missing_params_tier_empty()
    test_gate_passthrough_and_fail_closed()
    print("\n✓ A1 验证完成")


if __name__ == "__main__":
    main()
