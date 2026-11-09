"""批次 4 · P0-1 Harness 自学习影子验证 + 回滚 + 边界。

验收(执行计划/文稿附录 C):
  BLOCK 进 shadow_rules → 观察 N 次 → promote/discard;安全维度禁学。
覆盖(对应文稿「验证」四条 + 回滚/边界/持久化/事件):
  1. BLOCK(tool) → 规则进 shadow_rules,**不生效**(不进 rules、不重建索引)
  2. 影子观察:适用场景计数 observations;双轨 would_block/would_pass
  3. 观察达标 → promote(移入 rules、生效、history、事件)
  4. 误伤规则(would_block 率 > 0.3)→ discard;无意义规则(双零)→ discard
  5. 安全边界:semantic BLOCK 不生成规则(仅权重);未知维度 fail-closed 只记录
  6. rollback(rule_id):撤出生效、标记 rolled_back、不再生效
  7. 持久化:shadow_rules/history 落盘,重载后影子状态保留
  8. 事件:proposed/promoted/discarded/rolled_back 全发出
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("FOXCLAW_SKIP_DOTENV", "1")
os.environ.setdefault("HKC_EMBEDDING", "stub")

CHECKS = []


def check(cond, msg):
    CHECKS.append((bool(cond), msg))
    print(f"  {'✓' if cond else '✗'} {msg}")
    if not cond:
        raise AssertionError(f"断言失败: {msg}")


from foxclaw.core.thought_chain import ThoughtChain, ThoughtNode
from foxclaw.harness.harness import Harness


def _tc():
    return ThoughtChain(db_path=tempfile.mktemp(suffix=".db"))


def fresh(tc, promote_min_obs=3, baseline_entities=frozenset({"预算"})):
    """实判态 harness:跳过阈值校准观测期,固定阈值,小 promote 门槛便于测试。"""
    h = Harness(agent_id="t", thought_chain=tc,
                policy_path=tempfile.mktemp(suffix=".yaml"),
                shadow_rounds=0, promote_min_obs=promote_min_obs)
    h.register_goal("分析财务预算", allowed_tool_categories={"read", "finance"},
                    baseline_entities=baseline_entities)
    h._shadow_mode = False
    h._block_threshold, h._warn_threshold = 0.25, 0.18
    return h


def node(tc, tool=None, entities=None, content="继续分析预算"):
    n = ThoughtNode.create(session_id="s", agent_id="t", type="tool_intent",
                           content=content, conf=0.9,
                           tool_name=tool, entities=entities or [])
    tc.add_node(n)
    return n


def events_of(h):
    """收集 harness bus 本地发出的事件名序列。"""
    return getattr(h, "_test_events", [])


def _tap_events(h):
    h._test_events = []
    for ev in ("shadow_rule_proposed", "shadow_rule_promoted",
               "shadow_rule_discarded", "shadow_rule_rolled_back"):
        h.bus.on(ev, lambda data, _ev=ev: h._test_events.append(_ev))


def test_block_goes_to_shadow():
    print("\n【1】BLOCK → 影子候选(不生效)+ 双轨观察计数")
    tc = _tc()
    h = fresh(tc, promote_min_obs=3)
    _tap_events(h)

    r = h.check(node(tc, tool="delete_everything"))
    check(r.is_blocked, "越权工具被 BLOCK(行为不变)")
    check(not h._is_penalized_tool("delete_everything"),
          "影子期不生效:工具未被 penalize(未重建索引)")
    sh_rules = h._policy["shadow_rules"]
    check(len(sh_rules) == 1, "候选进入 shadow_rules(非 rules)")
    check(h._policy.get("rules", []) == [], "rules 仍为空(未生效)")
    sh = sh_rules[0]["shadow"]
    check(sh["status"] == "observing" and sh["observations"] == 0,
          "影子账本初始化 observing/0")
    check("shadow_rule_proposed" in events_of(h), "shadow_rule_proposed 事件已发")
    check(any(e["event"] == "propose" for e in h._policy["history"]),
          "history 记 propose")

    for _ in range(2):
        h.check(node(tc, tool="delete_everything"))
    sh = h._policy["shadow_rules"][0]["shadow"]
    check(sh["observations"] == 2, f"适用场景 observations 累积(实为 {sh['observations']})")
    h.check(node(tc, tool="delete_everything"))
    sh = h._policy["shadow_rules"][0]["shadow"]
    check(sh["status"] == "discarded",
          "双零证据(影子与 real 判定完全一致)→ 无意义 → discard")
    check("shadow_rule_discarded" in events_of(h), "shadow_rule_discarded 事件已发")
    check(h._policy.get("rules", []) == [], "discard 后 rules 仍为空")


def test_promote_after_evidence():
    print("\n【2】影子证据达标 → promote(生效 + history + 事件)")
    tc = _tc()
    h = fresh(tc, promote_min_obs=5)
    _tap_events(h)
    cand = {
        "id": "rule_manual_test",
        "trigger": {"harness_status": "BLOCK", "dominant_dim": "tool",
                    "delta_threshold": 0.3},
        "action": {"type": "penalize_tool", "tool_pattern": "suspicious_api",
                   "weight_delta": -0.3},
        "created_at": "2026-10-01T00:00:00+00:00",
        "source": "harness_learned", "agent_id": "t",
        "shadow": {"created_at": "2026-10-01T00:00:00+00:00",
                   "observations": 0, "would_block": 0, "would_pass": 0,
                   "status": "observing"},
    }
    h._policy["shadow_rules"].append(cand)

    orig = h.tool_drift.score
    h.tool_drift.score = lambda tool, allowed: 0.5 if tool == "suspicious_api" else orig(tool, allowed)
    try:
        r = h.check(node(tc, tool="suspicious_api"))
        check(not r.is_blocked, "real 判定 PASS(tool=0.5 未跨阈,影子不拦 real)")
        for i in range(4):
            h.check(node(tc, tool=f"other_tool_{i}"))
        rules = h._policy.get("rules", [])
        check(any(x["id"] == "rule_manual_test" for x in rules),
              "证据达标(wb/obs=1/5≤0.3)→ promote 到 rules")
        check(h._policy["shadow_rules"] == [], "shadow_rules 已清空(promote 移出)")
        check(h._is_penalized_tool("suspicious_api"),
              "promote 后规则生效(索引重建)")
        check(any(e["event"] == "promote" for e in h._policy["history"]),
              "history 记 promote")
        check("shadow_rule_promoted" in events_of(h), "shadow_rule_promoted 事件已发")
        r2 = h.check(node(tc, tool="suspicious_api"))
        check(r2.is_blocked, "promote 后同一工具被真实 BLOCK(规则级生效闭环)")
    finally:
        h.tool_drift.score = orig


def test_discard_harmful_rule():
    print("\n【3】误伤规则(would_block 率>0.3)→ discard")
    tc = _tc()
    h = fresh(tc, promote_min_obs=3)
    _tap_events(h)
    cand = {
        "id": "rule_harmful",
        "trigger": {"harness_status": "BLOCK", "dominant_dim": "tool",
                    "delta_threshold": 0.3},
        "action": {"type": "penalize_tool", "tool_pattern": "get_balance"},
        "created_at": "2026-10-01T00:00:00+00:00",
        "source": "harness_learned", "agent_id": "t",
        "shadow": {"created_at": "2026-10-01T00:00:00+00:00",
                   "observations": 0, "would_block": 0, "would_pass": 0,
                   "status": "observing"},
    }
    h._policy["shadow_rules"].append(cand)
    orig = h.tool_drift.score
    h.tool_drift.score = lambda tool, allowed: 0.5 if tool == "get_balance" else orig(tool, allowed)
    try:
        for _ in range(3):
            h.check(node(tc, tool="get_balance"))
        sh = h._policy["shadow_rules"][0]["shadow"]
        check(sh["status"] == "discarded",
              f"would_block 率 {sh['would_block']}/{sh['observations']} > 0.3 → discard")
        check(sh["would_block"] >= 2, "would_block 如实计数(影子会多拦)")
        check(not h._is_penalized_tool("get_balance"), "误伤规则未生效")
        check("shadow_rule_discarded" in events_of(h), "discard 事件已发")
    finally:
        h.tool_drift.score = orig


def test_safety_boundary():
    print("\n【4】安全边界:semantic 仅权重级 / 未知维度 fail-closed")
    tc = _tc()
    h = fresh(tc)
    _tap_events(h)

    w0 = dict(h.weights)
    r = h.check(node(tc, content="量子力学与黑洞信息悖论的数学形式化推导",
                     entities=["预算"]))
    if r.is_blocked and r.dominant_dim == "semantic":
        check(h._policy.get("shadow_rules", []) == [],
              "semantic BLOCK 不生成影子规则(仅权重级)")
        check(h.weights["semantic"] > w0["semantic"],
              "semantic 权重被上调(权重级学习保留)")
    else:
        from foxclaw.harness.harness import HarnessResult
        res = HarnessResult(status="BLOCK", delta=0.9, dominant_dim="semantic",
                            scores={"semantic": 0.9, "tool": 0.0, "scope": 0.0},
                            reason="t")
        h._trigger_policy_learning(node(tc), res)
        check(h._policy.get("shadow_rules", []) == [],
              "semantic BLOCK(直驱)不生成影子规则(仅权重级)")
        check(h.weights["semantic"] > w0["semantic"], "semantic 权重上调")

    from foxclaw.harness.harness import HarnessResult
    w1 = dict(h.weights)
    res = HarnessResult(status="BLOCK", delta=0.9, dominant_dim="financial",
                        scores={}, reason="t")
    h._trigger_policy_learning(node(tc), res)
    check(h.weights == w1, "未知维度 fail-closed:权重不动")
    check(any(e["event"] == "skip" for e in h._policy["history"]),
          "未知维度只记录(history skip)")
    check(h._policy.get("shadow_rules", []) == [], "未知维度不生成规则")
    check("shadow_rule_proposed" not in events_of(h), "未知维度不发 propose 事件")


def test_rollback():
    print("\n【5】rollback:撤出生效 + 标记 rolled_back + 不再生效")
    tc = _tc()
    h = fresh(tc)
    _tap_events(h)
    h._policy["rules"].append({
        "id": "rule_rb", "trigger": {"dominant_dim": "tool"},
        "action": {"type": "penalize_tool", "tool_pattern": "bad_tool"},
        "source": "harness_learned", "agent_id": "t"})
    h._reindex_learned_actions()
    check(h._is_penalized_tool("bad_tool"), "前提:规则生效中")

    ok = h.rollback("rule_rb")
    check(ok, "rollback 返回 True")
    check(not h._is_penalized_tool("bad_tool"), "回滚后不再生效(索引已重建)")
    check(h._policy["rules"] == [], "rules 已移除该规则")
    rb = h._policy["shadow_rules"][0]
    check(rb["shadow"]["status"] == "rolled_back", "规则标记 rolled_back(保留可审计)")
    check(any(e["event"] == "rollback" for e in h._policy["history"]),
          "history 记 rollback")
    check("shadow_rule_rolled_back" in events_of(h), "rolled_back 事件已发")
    check(h.rollback("nonexistent") is False, "找不到规则返回 False")

    h.check(node(tc, tool="bad_tool"))
    check(h._policy["shadow_rules"][0]["shadow"]["observations"] == 0,
          "rolled_back 不再参与影子观察")


def test_persistence():
    print("\n【6】持久化:shadow/history 落盘,重载保留")
    tc = _tc()
    p = tempfile.mktemp(suffix=".yaml")
    h = Harness(agent_id="t", thought_chain=tc, policy_path=p,
                shadow_rounds=0, promote_min_obs=3)
    h.register_goal("分析财务预算", allowed_tool_categories={"read", "finance"},
                    baseline_entities=frozenset({"预算"}))
    h._shadow_mode = False
    h.check(node(tc, tool="delete_everything"))

    import yaml
    doc = yaml.safe_load(open(p, encoding="utf-8"))
    check("shadow_rules" in doc and len(doc["shadow_rules"]) == 1,
          "shadow_rules 已落盘")
    check("history" in doc and any(e["event"] == "propose" for e in doc["history"]),
          "history 已落盘")

    h2 = Harness(agent_id="t", thought_chain=tc, policy_path=p,
                 shadow_rounds=0, promote_min_obs=3)
    h2.register_goal("分析财务预算", allowed_tool_categories={"read", "finance"},
                     baseline_entities=frozenset({"预算"}))
    h2._shadow_mode = False
    check(len(h2._policy["shadow_rules"]) == 1
          and h2._policy["shadow_rules"][0]["shadow"]["status"] == "observing",
          "重载后影子规则与观察状态保留")
    check(not h2._is_penalized_tool("delete_everything"),
          "重载后影子仍未生效")
    os.unlink(p)


def main():
    print("=" * 60)
    print("批次 4 · P0-1 影子学习(shadow→promote/discard+回滚+边界)")
    print("=" * 60)
    test_block_goes_to_shadow()
    test_promote_after_evidence()
    test_discard_harmful_rule()
    test_safety_boundary()
    test_rollback()
    test_persistence()
    print("\n" + "=" * 60)
    print(f"✓ 全部 {len(CHECKS)} 项断言通过")
    print("=" * 60)


if __name__ == "__main__":
    main()
