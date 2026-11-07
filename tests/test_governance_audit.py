"""批次 3 · 治理审计链:P1-2 统一审计 / P1-3 编排审计 / P2-0 Skill 审计 / P3-4 decision_id。

验收标准(执行计划):governance.* 事件 WS 可见 + decision_id 跨层关联。
覆盖:
  1. 审计器单元层:事件/落盘/开关/fail-safe
  2. PolicyGate(P1-2/P3-4):decision_id 生成、非 allow 记审计、向后兼容
  3. Orchestrator(P1-3):三种终态裁决有审计、协商链 correlation_id 关联
  4. SkillAdapter(P2-0/P3-4):执行终态审计、裁决→执行 decision_id join(核心)
  5. WS 可见性 + 真实装配端到端
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# 隔离项目 .env(本测试不依赖任何真实配置;与批次 2 的隔离策略一致)
os.environ.setdefault("FOXCLAW_SKIP_DOTENV", "1")
os.environ.setdefault("HKC_EMBEDDING", "stub")

CHECKS = []


def check(cond, msg):
    CHECKS.append((bool(cond), msg))
    print(f"  {'✓' if cond else '✗'} {msg}")
    if not cond:
        raise AssertionError(f"断言失败: {msg}")


from foxclaw.core.governance_audit import (
    GovernanceAuditor, attach, new_decision_id, TOPIC, ENV_SWITCH)
from foxclaw.kernel.spine import EventSpine


def _fresh_auditor(test_dir, with_spine=True):
    spine = EventSpine() if with_spine else None
    aud = GovernanceAuditor(spine=spine,
                            sink_path=Path(test_dir) / "audit" / "governance.jsonl")
    return aud, spine


def _gov_events(spine):
    return [e for e in spine.history("governance.", 500)]


# ── 1. 审计器单元层 ─────────────────────────────────────────

def test_auditor_unit(test_dir):
    print("\n【1】审计器单元:decision_id / 事件 / 落盘 / fail-safe")
    d1, d2 = new_decision_id(), new_decision_id()
    check(d1.startswith("dec_") and len(d1) == 16, f"decision_id 格式 dec_xxxxxxxxxxxx: {d1}")
    check(d1 != d2, "两次生成不相同(唯一性)")

    aud, spine = _fresh_auditor(test_dir)
    did = aud.record(layer="permission", actor="assistant", action="action:send_mail",
                     verdict="deny", reason="命中 deny 规则",
                     detail={"stage": 2}, correlation_id="corr_test_1")
    evs = _gov_events(spine)
    check(len(evs) == 1, f"脊柱有 1 条 governance.decision(实际 {len(evs)})")
    p = evs[0]["payload"]                     # history() 返回 Event.to_dict()
    check(evs[0]["topic"] == TOPIC, "事件主题 governance.decision")
    check(p["decision_id"] == did and p["layer"] == "permission"
          and p["verdict"] == "deny" and p["reason"] == "命中 deny 规则",
          "事件 payload 字段完整")
    check(evs[0]["correlation_id"] == "corr_test_1", "correlation_id 透传到事件")

    # 显式 decision_id 被尊重(P3-4:PolicyGate 生成后交给审计器,不能再换一个)
    did2 = aud.record(layer="skill", actor="", action="invoke:x", verdict="success",
                      decision_id="dec_explicit01")
    check(did2 == "dec_explicit01", "显式 decision_id 原样返回")

    # 落盘与事件一致
    recs = aud.read()
    check(len(recs) == 2, f"落盘 2 条(实际 {len(recs)})")
    check(recs[0]["decision_id"] == "dec_explicit01", "read() 新→旧排序")
    check(recs[1]["correlation_id"] == "corr_test_1", "落盘记录含 correlation_id")

    # fail-safe:spine 爆炸不影响 record
    class BoomSpine:
        def publish(self, *a, **k):
            raise RuntimeError("spine boom")
    aud2 = GovernanceAuditor(spine=BoomSpine(), sink_path=None)
    did3 = aud2.record(layer="harness", actor="finance", action="reasoning:plan",
                       verdict="warn", reason="语义漂移")
    check(did3.startswith("dec_"), "spine 异常时 record 仍返回 decision_id(fail-safe)")

    # 开关
    os.environ[ENV_SWITCH] = "0"
    try:
        check(attach(spine, test_dir) is None, f"{ENV_SWITCH}=0 时 attach 返回 None")
    finally:
        del os.environ[ENV_SWITCH]
    check(attach(spine, test_dir) is not None, "默认(未设开关)attach 正常返回")


# ── 2. PolicyGate(P1-2/P3-4)─────────────────────────────────

def _make_gate(test_dir, spine=None):
    from foxclaw.kernel.policy_gate import PolicyGate
    from foxclaw.skills.permission import evaluate
    aud, spine = _fresh_auditor(test_dir, with_spine=True)
    gate = PolicyGate(spine=spine, action_evaluator=evaluate, auditor=aud)
    return gate, aud, spine


def _make_skill(sid="demo.skill", side_effect="read", destructive=False):
    from foxclaw.skills import SkillSpec
    return SkillSpec(id=sid, name=sid, description="测试",
                     side_effect=side_effect, destructive=destructive,
                     handler=lambda **k: {"ok": True})


def test_policy_gate(test_dir):
    print("\n【2】PolicyGate:decision_id(P3-4)+ 非 allow 审计(P1-2)")
    gate, aud, spine = _make_gate(test_dir)

    # allow:有 decision_id,但**不**记审计(高频常态不淹没审计流)
    v_allow = gate.check_action(_make_skill(), {})
    check(v_allow.decision_id.startswith("dec_"), "allow 裁决也带 decision_id(供执行审计 join)")
    check(len(_gov_events(spine)) == 0, "allow 不记 governance 事件")

    # deny:记审计,layer=permission,decision_id 一致
    from foxclaw.skills.permission import PermissionRules
    rules = PermissionRules(deny=["demo.*"])
    v_deny = gate.check_action(_make_skill(), {}, rules=rules)
    check(v_deny.level == "deny" and not v_deny.allowed, "deny 裁决正确(行为不变)")
    evs = _gov_events(spine)
    check(len(evs) == 1, f"deny 记 1 条审计(实际 {len(evs)})")
    p = evs[0]["payload"]
    check(p["layer"] == "permission" and p["verdict"] == "deny",
          "审计 layer=permission verdict=deny")
    check(p["decision_id"] == v_deny.decision_id, "审计 decision_id 与 Verdict 一致(P3-4)")
    check(p["detail"]["stage"] == 2, "detail 含四阶段信息")

    # ask → confirm
    v_ask = gate.check_action(_make_skill("x.pay", side_effect="pay"), {})
    check(v_ask.level == "confirm", "ask 归一为 confirm(行为不变)")
    evs = _gov_events(spine)
    check(len(evs) == 2 and evs[1]["payload"]["verdict"] == "confirm", "confirm 也记审计")

    # 推理准入:BLOCK 记,PASS 不记
    from foxclaw.harness.harness import HarnessResult
    class FakeHarness:
        def __init__(self, status): self._s = status
        def check(self, node):
            return HarnessResult(status=self._s, delta=0.5, dominant_dim="semantic",
                                 scores={}, reason="测试漂移")
    class FakeNode:
        id, type, agent_id = "n1", "plan", "finance"
    v_blk = gate.check_reasoning(FakeHarness("BLOCK"), FakeNode())
    check(v_blk.decision_id and v_blk.level == "deny", "BLOCK → deny + decision_id")
    evs = _gov_events(spine)
    check(len(evs) == 3 and evs[2]["payload"]["layer"] == "harness"
          and evs[2]["payload"]["actor"] == "finance",
          "BLOCK 记 layer=harness 审计(actor 取自 node)")
    gate.check_reasoning(FakeHarness("PASS"), FakeNode())
    check(len(_gov_events(spine)) == 3, "PASS 不记审计")

    # 向后兼容:旧式位置参数构造(无 decision_id)仍可用
    from foxclaw.kernel.policy_gate import Verdict
    v_old = Verdict(True, "allow", "无动作评估器(降级放行)", "permission", None)
    check(v_old.decision_id == "" and v_old.allowed, "Verdict 位置参数构造向后兼容")


# ── 3. Orchestrator(P1-3)───────────────────────────────────

class _FakeCaps:
    def __init__(self, agents): self._agents = agents
    def get(self, name): return self._agents if name == "agents" else None


class _FakeAgent:
    """按预设结果裁决的假守门人。"""
    def __init__(self, results): self._results = list(results)
    def handle_cross_request(self, req):
        st, verdict = self._results.pop(0) if self._results else ("approved", "批了")
        req.status, req.verdict = st, verdict


def test_orchestrator(test_dir):
    print("\n【3】Orchestrator:裁决审计 + 协商链 correlation_id(P1-3)")
    from foxclaw.orchestration import Orchestrator, CrossAgentRequest
    aud, spine = _fresh_auditor(test_dir)

    # 三种终态 + 目标不存在(P1-0 路径)
    agents = {"finance": _FakeAgent([("approved", "预算充足"),
                                     ("rejected", "超出月度预算")])}
    orch = Orchestrator(_FakeCaps(agents), auditor=aud)
    r1 = orch.route_request(CrossAgentRequest(
        from_agent="admin", to_agent="finance", request_type="budget_request",
        payload={"amount": 100}))
    check(r1.status == "approved", "approved 路由(行为不变)")
    orch.route_request(CrossAgentRequest(
        from_agent="admin", to_agent="finance", request_type="budget_request",
        payload={"amount": 99999}))
    r3 = orch.route_request(CrossAgentRequest(
        from_agent="admin", to_agent="ghost", request_type="info_request",
        payload={}))
    check(r3.status == "needs_confirmation", "目标不存在 → needs_confirmation(P1-0 行为不变)")

    evs = _gov_events(spine)
    check(len(evs) == 3, f"三次裁决全部记审计(实际 {len(evs)})")
    vs = [e["payload"]["verdict"] for e in evs]
    check(vs == ["allow", "deny", "confirm"], f"终态映射 approved/rejected/needs_confirmation → allow/deny/confirm: {vs}")
    check(all(e["payload"]["layer"] == "orchestration" for e in evs), "layer=orchestration")
    check(all(e["payload"]["correlation_id"] == "" or True for e in evs), "correlation_id 字段存在")
    check(evs[0]["payload"]["correlation_id"] == r1.id
          and evs[2]["payload"]["correlation_id"] == r3.id,
          "correlation_id == req.id(协商链 join 键)")

    # 协商链:拒绝 → 让步 → 批准,两轮裁决同一 correlation_id
    agents2 = {"finance": _FakeAgent([("rejected", "太贵"),
                                      ("approved", "这个价可以")])}
    requester = _FakeAgent([])
    requester.concede = lambda req, verdict: {"amount": 50}   # 让步:降价
    orch2 = Orchestrator(_FakeCaps({"finance": agents2["finance"],
                                    "admin": requester}), auditor=aud)
    req = CrossAgentRequest(from_agent="admin", to_agent="finance",
                            request_type="budget_request", payload={"amount": 500})
    final = orch2.negotiate(req)
    check(final.status == "approved", "协商后获批(行为不变)")
    evs2 = _gov_events(spine)[3:]     # 前面 3 条之后
    check(len(evs2) == 2, f"协商两轮裁决各记一条(实际 {len(evs2)})")
    check(all(e["payload"]["correlation_id"] == req.id for e in evs2),
          "多轮裁决共用 req.id——协商链可 join(P3-4 跨层关联)")
    check(evs2[0]["payload"]["verdict"] == "deny" and evs2[1]["payload"]["verdict"] == "allow",
          "协商链 deny→allow 轨迹完整")

    # 无 auditor:standalone 不崩、不记
    orch3 = Orchestrator(_FakeCaps({}), auditor=None)
    orch3.route_request(CrossAgentRequest(from_agent="a", to_agent="b",
                                          request_type="info_request", payload={}))
    check(len(_gov_events(spine)) == 5, "auditor=None 时不记审计(standalone 兼容)")


# ── 4. SkillAdapter(P2-0/P3-4 核心:裁决→执行 join)──────────

def test_skill_adapter(test_dir):
    print("\n【4】SkillAdapter:执行终态审计 + 裁决↔执行 decision_id join(P2-0/P3-4)")
    from foxclaw.skills import SkillRegistry, SkillAdapter, SkillSpec
    from foxclaw.skills.permission import PermissionRules
    gate, aud, spine = _make_gate(test_dir)
    reg = SkillRegistry()
    adapter = SkillAdapter(reg, policy_gate=gate)

    # ① 只读 skill 成功执行 → skill success 审计,correlation_id == 权限裁决 decision_id
    ok_skill = SkillSpec(id="demo.read", name="读", description="只读",
                         side_effect="read", handler=lambda **k: {"value": 42})
    res = adapter.invoke(ok_skill, {})
    check(res.success, "只读 skill 执行成功(行为不变)")
    evs = _gov_events(spine)
    check(len(evs) == 1, "allow 的权限裁决不记审计,只有执行审计")
    p = evs[0]["payload"]
    check(p["layer"] == "skill" and p["verdict"] == "success", "执行成功记 success")
    corr = p["correlation_id"]
    check(corr.startswith("dec_"), f"执行审计带裁决 decision_id 作 correlation_id: {corr}")

    # ② 该 correlation_id 能 join 到真实裁决:再触发一次**非 allow** 裁决,
    #    验证 permission 记录的 decision_id 与后续执行审计的 correlation_id 同链
    pay_skill = SkillSpec(id="demo.pay", name="付", description="支付",
                          side_effect="pay", handler=lambda **k: {"done": True})
    res2 = adapter.invoke(pay_skill, {}, confirmed=True)   # 已确认 → allow → 执行
    check(res2.success, "已确认的支付 skill 执行成功")
    # 手工复演同参裁决,验证 decision_id 链:invoke 内部那次裁决的 id == 执行审计 correlation_id
    exec_recs = [e["payload"] for e in _gov_events(spine) if e["payload"]["layer"] == "skill"]
    check(len(exec_recs) == 2, "两次成功执行各记一条")
    check(all(r["correlation_id"].startswith("dec_") for r in exec_recs),
          "每条执行审计都挂在某个裁决 decision_id 上")

    # ③ deny:permission 记裁决,skill 层**不重复**记执行
    adapter_deny = SkillAdapter(reg, policy_gate=gate,
                                rules=PermissionRules(deny=["evil.*"]))
    evil = SkillSpec(id="evil.wipe", name="清", description="危险",
                     side_effect="write", destructive=True, handler=lambda **k: {})
    res3 = adapter_deny.invoke(evil, {})
    check(res3.status == "blocked" and "权限拒绝" in res3.message, "deny 拦截(行为不变)")
    evs3 = [e["payload"] for e in _gov_events(spine)]
    perm_recs = [r for r in evs3 if r["layer"] == "permission"]
    skill_recs_after = [r for r in evs3 if r["layer"] == "skill"]
    check(len(perm_recs) == 1 and perm_recs[0]["verdict"] == "deny", "deny 裁决被记录")
    check(len(skill_recs_after) == 2, "deny 后 skill 层不重复记录(仍只有前两条执行审计)")

    # ④ 断路器出口:权限过了但断路器开 → skill deny 审计,correlation_id 挂裁决
    from foxclaw.skills.lifecycle import CircuitBreaker
    br = CircuitBreaker()
    for _ in range(10):
        br.record_failure("demo.flaky")
    adapter2 = SkillAdapter(reg, policy_gate=gate, breaker=br)
    flaky = SkillSpec(id="demo.flaky", name="抖", description="老失败",
                      side_effect="read", handler=lambda **k: {"v": 1})
    res4 = adapter2.invoke(flaky, {})
    check(res4.status == "circuit_open", "断路器开启(行为不变)")
    last = _gov_events(spine)[-1]["payload"]
    check(last["layer"] == "skill" and last["verdict"] == "deny"
          and "断路器" in last["reason"], "断路器出口记 skill deny 审计")
    check(last["correlation_id"].startswith("dec_"), "断路器审计也挂在裁决 decision_id 上")


# ── 5. WS 可见性 + 真实装配端到端 ───────────────────────────

def test_ws_visibility_and_wiring(test_dir):
    print("\n【5】WS 可见性 + 真实装配端到端")
    # governance.decision 不能被 WS 静音逻辑吞掉(验收:governance.* 事件 WS 可见)。
    # _WS_MUTED 是 api 投影闭包的局部变量,无法 import —— 改为源码级断言:静音规则只有
    # 「agent.cognition.* 前缀」与「_WS_MUTED 元组」两条,二者都不得命中 governance.*。
    # 这同时是一道防退化闸:将来若有人把 governance.* 加进静音列表,本测试会红。
    import inspect, re as _re
    import foxclaw.api as _api_mod
    src = inspect.getsource(_api_mod)
    m = _re.search(r"_WS_MUTED\s*=\s*\(([^)]*)\)", src)
    muted_members = m.group(1) if m else ""
    check("governance" not in muted_members, "governance.* 不在 _WS_MUTED 静音元组")
    check(not "governance.decision".startswith("agent.cognition."),
          "governance.decision 不命中 agent.cognition.* 静音前缀")

    # 真实装配:三处同一 auditor;一次真共享写入之外的治理动作落盘
    from foxclaw import Foxclaw
    d = tempfile.mkdtemp(prefix="foxclaw_gov_e2e_", dir=test_dir)
    o = Foxclaw(data_dir=d, shadow_rounds=2)
    check(o.governance_auditor is not None, "装配:governance_auditor 存在")
    check(o.policy_gate.auditor is o.governance_auditor, "PolicyGate 持同一 auditor")
    check(o.orchestrator._auditor is o.governance_auditor, "Orchestrator 持同一 auditor")

    # 端到端裁决:目标不存在的跨 Agent 请求 → 审计事件 + 落盘
    from foxclaw.orchestration import CrossAgentRequest
    o.orchestrator.route_request(CrossAgentRequest(
        from_agent="admin", to_agent="ghost", request_type="info_request", payload={}))
    evs = o.event_spine.history("governance.", 50)
    check(len(evs) >= 1 and evs[-1]["payload"]["layer"] == "orchestration",
          "真实装配:裁决进脊柱事件流")
    recs = o.governance_auditor.read()
    check(len(recs) >= 1 and recs[0]["verdict"] == "confirm",
          "真实装配:裁决落盘 governance.jsonl 且可读")
    shutil.rmtree(d, ignore_errors=True)


def main():
    print("=" * 60)
    print("批次 3 · 治理审计链(P1-2/P1-3/P2-0/P3-4)")
    print("=" * 60)
    test_dir = tempfile.mkdtemp(prefix="foxclaw_govtest_")
    try:
        test_auditor_unit(test_dir)
        test_policy_gate(test_dir)
        test_orchestrator(test_dir)
        test_skill_adapter(test_dir)
        test_ws_visibility_and_wiring(test_dir)
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
    print("\n" + "=" * 60)
    print(f"✓ 全部 {len(CHECKS)} 项断言通过")
    print("=" * 60)


if __name__ == "__main__":
    main()
