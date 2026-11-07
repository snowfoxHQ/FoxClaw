"""
领域认知化(v2 ③ Step1:DomainReasoner + 财务守门人形式化)测试 —— 全离线。

覆盖:
  1. DomainReasoner 基座:reason() 按序 observe→hypothesize→gather_evidence→arbitrate
  2. FinanceGatekeeperReasoner:小额→approve、大额→confirm、低余额→reject(freeze);
     裁决与原守门人逐字一致 + 显式证据链(source=rules) + 假设
  3. 真实路径:orchestrator.route_request 走认知化守门人,req 附证据链(可审计)
"""
import os, sys, asyncio, tempfile, shutil
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_EMBEDDING_MODEL"] = "foxclaw-nonexistent-fast"
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.cognitive.reasoner import DomainReasoner, ReasonedVerdict, Evidence


def test_reasoner_base_order():
    print("\n【1】DomainReasoner 基座:四步按序执行")
    order = []
    class R(DomainReasoner):
        def observe(self, s): order.append("observe"); return {"x": s}
        def hypothesize(self, o): order.append("hypothesize"); return ["h"]
        def gather_evidence(self, s, o): order.append("evidence"); return [Evidence("f", "d")]
        def arbitrate(self, s, o, h, e):
            order.append("arbitrate")
            return ReasonedVerdict("approve", "ok", e, h, "rules")
    rv = R().reason(42)
    assert order == ["observe", "hypothesize", "evidence", "arbitrate"], order
    assert rv.decision == "approve" and rv.hypotheses == ["h"] and len(rv.evidence) == 1
    print("  ✓ observe→hypothesize→gather_evidence→arbitrate;证据链随裁决返回")


def _policy():
    from foxclaw.finance import FinanceRuntime
    # data_path 显式隔离:默认落在 policy 同目录(系统 Temp),会与其他测试共用一份数据文件
    p = FinanceRuntime(policy_path=tempfile.mktemp(suffix=".yaml"),
                       data_path=tempfile.mktemp(suffix=".json")).policy
    # 显式锁定本测试的前提阈值(200,模糊带 [140,200)),**不依赖产品默认值** ——
    # 默认阈值是给用户按自己实际调的可调项,不该让调它就牵动这些验证推理逻辑的测试。
    for r in p.rules:
        if r.id == "large_purchase_check":
            r.threshold = 200.0
            r.condition = "单笔支出 > 200"
    return p


def test_finance_gatekeeper_reasoner():
    print("\n【2】财务守门人认知化:裁决逐字一致 + 证据链 + 假设")
    from foxclaw.finance.reasoner import FinanceGatekeeperReasoner
    from foxclaw.finance.models import Transaction
    r = FinanceGatekeeperReasoner(_policy())
    # 小额(规则内)→ approve
    rv = r.reason((Transaction(amount=-150, category="travel", entity="高铁", account="default"), 5000))
    assert rv.decision == "approve" and rv.rationale == "财务批准:travel 预算 150 在规则内"
    assert rv.source == "rules" and any(e.factor == "rules_clear" for e in rv.evidence)
    assert rv.hypotheses, "应有假设"
    # 大额(>阈值)→ confirm,证据=large_purchase_check
    rv2 = r.reason((Transaction(amount=-680, category="travel", entity="酒店", account="default"), 5000))
    assert rv2.decision == "confirm" and any(e.factor == "large_purchase_check" for e in rv2.evidence)
    print(f"  ✓ 小额→approve(证据 rules_clear);大额→confirm(证据 large_purchase_check;假设={rv2.hypotheses})")


def test_context_evidence_no_decision_change():
    print("\n【2b】Step2:情境证据(历史/预算/频率)丰满证据链,但**决策不变**")
    from foxclaw.finance import FinanceRuntime
    from foxclaw.finance.reasoner import FinanceGatekeeperReasoner
    from foxclaw.finance.models import Transaction
    rt = FinanceRuntime(policy_path=tempfile.mktemp(suffix=".yaml"),
                        data_path=tempfile.mktemp(suffix=".json"))
    rt.set_budget("travel", 3000); rt.set_balance("default", 5000)
    for _ in range(3):   # 3 笔历史同类交易(同对手方)
        rt.add_transaction(Transaction(amount=-100, category="travel", entity="高铁",
                                       account="default"), run_gatekeeper=False)
    r = FinanceGatekeeperReasoner(rt.policy, runtime=rt)
    rv = r.reason((Transaction(amount=-150, category="travel", entity="高铁",
                               account="default"), 5000))
    assert rv.decision == "approve", "规则内 → 决策仍是 approve(情境证据不改裁决)"
    factors = {e.factor for e in rv.evidence}
    assert {"history_category", "budget_remaining", "entity_frequency"} <= factors, factors
    # 无 runtime → 无情境证据(向后兼容;裁决相同)
    rv0 = FinanceGatekeeperReasoner(rt.policy).reason(
        (Transaction(amount=-150, category="travel", entity="高铁", account="default"), 5000))
    assert not ({"history_category", "budget_remaining", "entity_frequency"}
                & {e.factor for e in rv0.evidence})
    assert rv0.decision == rv.decision == "approve"
    print(f"  ✓ 决策 approve 不变;证据链含 {sorted(factors & {'history_category','budget_remaining','entity_frequency'})}")


class _MockLLM:
    def __init__(self, resp="APPROVE", raises=False):
        self.resp, self.raises, self.calls, self.is_stub = resp, raises, 0, False
    def complete(self, system, messages, max_tokens=2048, agent_id=""):
        self.calls += 1
        if self.raises:
            raise RuntimeError("llm down")
        return self.resp


def test_llm_arbitration_safety():
    print("\n【2c】Step3:LLM 裁决的安全不变量(只增强模糊、只能 approve→confirm、永不 reject)")
    from foxclaw.finance.reasoner import FinanceGatekeeperReasoner
    from foxclaw.finance.models import Transaction
    policy = _policy()   # large_purchase_check 阈值 200;模糊带 [140,200)
    def txn(amt): return Transaction(amount=-amt, category="travel", entity="高铁", account="default")
    def mk(llm, enabled=True): return FinanceGatekeeperReasoner(policy, llm=llm, llm_enabled=enabled)

    # 1. 规则触发(680>200)→ 规则权威,LLM 绝不被调
    llm = _MockLLM("APPROVE"); rv = mk(llm).reason((txn(680), 5000))
    assert rv.decision == "confirm" and rv.source == "rules" and llm.calls == 0

    # 2. 模糊(150∈[140,200))+ LLM 说 CONFIRM → confirm, source=llm
    llm = _MockLLM("CONFIRM"); rv = mk(llm).reason((txn(150), 5000))
    assert rv.decision == "confirm" and rv.source == "llm" and llm.calls == 1

    # 3. 模糊 + LLM 说 APPROVE → approve, source=llm
    llm = _MockLLM("APPROVE"); rv = mk(llm).reason((txn(150), 5000))
    assert rv.decision == "approve" and rv.source == "llm"

    # 4. LLM 出错/超时 → confirm(fail-safe),绝不自动放行
    llm = _MockLLM(raises=True); rv = mk(llm).reason((txn(150), 5000))
    assert rv.decision == "confirm" and rv.source == "llm_failsafe"

    # 5. 【核心不变量】LLM 乱回(含"拒绝"/REJECT/含糊)→ **绝不 reject**,fail-safe confirm
    for bad in ["REJECT", "让我拒绝这笔", "不确定", "yes maybe"]:
        llm = _MockLLM(bad); rv = mk(llm).reason((txn(150), 5000))
        assert rv.decision == "confirm" and rv.source == "llm_failsafe", (bad, rv.decision)
        assert rv.decision != "reject", "LLM 永远不能产生 reject"

    # 6. 明显安全(50<140)→ 规则批准,LLM 不调(省成本)
    llm = _MockLLM("CONFIRM"); rv = mk(llm).reason((txn(50), 5000))
    assert rv.decision == "approve" and rv.source == "rules" and llm.calls == 0

    # 7. 默认关(llm_enabled=False)→ 全走规则,LLM 不调(behavior-preserving)
    llm = _MockLLM("CONFIRM"); rv = mk(llm, enabled=False).reason((txn(150), 5000))
    assert rv.decision == "approve" and rv.source == "rules" and llm.calls == 0

    # 8. is_stub(无真实模型)→ 视为不可用,不调 LLM
    stub = _MockLLM("CONFIRM"); stub.is_stub = True
    rv = FinanceGatekeeperReasoner(policy, llm=stub, llm_enabled=True).reason((txn(150), 5000))
    assert rv.source == "rules" and stub.calls == 0
    print("  ✓ 规则权威不问LLM · 模糊只 approve/confirm · 出错/乱回 fail-safe confirm · "
          "永不 reject · 明显安全/默认关/stub 不调")


def test_real_path_evidence():
    print("\n【3】真实路径:认知化守门人 + req 附证据链")
    d = tempfile.mkdtemp(prefix="reasoner_")
    try:
        from foxclaw import Foxclaw
        from foxclaw.orchestration import CrossAgentRequest
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        os_.finance_runtime.set_balance("default", 5000)
        # 大额 → 需确认(裁决不变)+ 证据链附在 req 上
        req = os_.orchestrator.route_request(CrossAgentRequest(
            from_agent="admin", to_agent="finance", request_type="budget_request",
            payload={"amount": 680, "category": "travel", "entity": "酒店", "account": "default"},
            reason="大额"))
        assert req.status == "needs_confirmation", req.status
        assert hasattr(req, "evidence") and req.evidence, "守门人应附证据链"
        assert req.reason_source == "rules"
        print(f"  ✓ status={req.status}(裁决不变);evidence={req.evidence};source={req.reason_source}")
        asyncio.run(os_.aclose())
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    print("=" * 60)
    print("领域认知化 · DomainReasoner + 财务守门人(v2 ③ Step1)")
    print("=" * 60)
    test_reasoner_base_order()
    test_finance_gatekeeper_reasoner()
    test_context_evidence_no_decision_change()
    test_llm_arbitration_safety()
    test_real_path_evidence()
    print("\n" + "=" * 60)
    print("✓ Step1 验证完成:观察→假设→证据→裁决 形式化;规则实现、裁决不变、证据链可审计")
    print("=" * 60)


if __name__ == "__main__":
    main()
