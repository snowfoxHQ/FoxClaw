"""
FinanceGatekeeperReasoner —— 财务预算守门人的认知化(v2 ③ Step1)。

把原本"policy.evaluate_transaction + veto/freeze→reject / else→confirm / none→approve"
的规则裁决,形式化为 观察→假设→证据→裁决,**裁决逐字不变**、并显式产出证据链。
Step1 用规则实现(source=rules);Step3 会给**模糊情形**叠加 LLM 裁决(双层保留:
规则对硬约束永远权威,LLM 只增强模糊、且 fail-safe)。
"""
from __future__ import annotations
from ..cognitive.reasoner import DomainReasoner, ReasonedVerdict, Evidence


class FinanceGatekeeperReasoner(DomainReasoner):
    """subject = (txn, account_balance)。"""

    # Step3:LLM 只裁决"逼近阈值"的模糊带 [band×阈值, 阈值)——低于此=明显安全(省成本),
    # 高于阈值=规则已触发确认(不在本路径)。
    AMBIGUITY_BAND = 0.7

    def __init__(self, policy, runtime=None, llm=None, llm_enabled: bool = False):
        self.policy = policy
        self.runtime = runtime      # 可选:FinanceRuntime,用于 Step2 的情境证据(历史/预算/频率)
        # Step3:模糊情形的 LLM 裁决器(可选、默认关)。llm 需有 complete(system,messages)->str + is_stub。
        self.llm = llm
        self.llm_enabled = bool(llm_enabled) and llm is not None and not getattr(llm, "is_stub", True)

    def observe(self, subject) -> dict:
        txn, bal = subject
        return {"amount": abs(txn.amount), "category": txn.category,
                "is_expense": txn.is_expense, "balance": bal, "entity": txn.entity}

    def hypothesize(self, obs: dict) -> list:
        h = []
        if obs.get("is_expense"):
            h.append("可能大额/异常支出")
        if obs.get("balance") is not None:
            h.append("可能触及低余额保护")
        return h or ["常规支出"]

    def gather_evidence(self, subject, obs: dict) -> list:
        txn, bal = subject
        verdicts = self.policy.evaluate_transaction(txn, bal)   # 规则=裁决证据(raw 非空)
        ev = [Evidence(factor=v.rule_id, detail=v.message, weight=1.0, raw=v)
              for v in verdicts]
        if not verdicts:
            ev.append(Evidence(factor="rules_clear", detail="未触发任何财务规则", weight=0.5))
        # Step2:结构化情境证据(informational,raw=None → **不参与裁决**,只让证据链更丰满)
        ev += self._context_evidence(txn)
        return ev

    def _context_evidence(self, txn) -> list:
        """从财务内核数据采集情境证据:历史同类交易 / 预算余量 / 对手方频率。
        全部 informational(raw=None):裁决仍只看规则,决策逐字不变。任一采集失败都跳过。"""
        rt = self.runtime
        if rt is None:
            return []
        out = []
        try:
            same_cat = [t for t in getattr(rt, "transactions", []) if t.category == txn.category]
            if same_cat:
                total = sum(abs(t.amount) for t in same_cat)
                out.append(Evidence("history_category",
                    f"该类别历史 {len(same_cat)} 笔,累计 {total:.0f}", 0.4))
                budget = next((b for b in getattr(rt, "budgets", [])
                               if b.category == txn.category), None)
                if budget is not None:
                    spent = sum(abs(t.amount) for t in same_cat if t.is_expense)
                    out.append(Evidence("budget_remaining",
                        f"预算 {budget.monthly_limit:.0f},已花 {spent:.0f},余 {budget.monthly_limit - spent:.0f}",
                        0.6))
            ent = getattr(rt, "entities", {}).get(txn.entity)
            if ent is not None:
                out.append(Evidence("entity_frequency",
                    f"对手方「{txn.entity}」历史 {getattr(ent, 'txn_count', 0)} 次", 0.3))
        except Exception:
            pass
        return out

    def arbitrate(self, subject, obs: dict, hyps: list, ev: list) -> ReasonedVerdict:
        triggered = [e.raw for e in ev if e.raw is not None]
        if triggered:
            # 【硬约束权威】规则触发 → 规则说了算(reject/confirm),**绝不问 LLM**。
            actions = [v.action for v in triggered]
            decision = "reject" if ("veto" in actions or "freeze" in actions) else "confirm"
            return ReasonedVerdict(decision, " / ".join(v.message for v in triggered),
                                   ev, hyps, "rules")
        # 规则批准:若"逼近阈值"的模糊情形且启用了 LLM,让 LLM 就证据复核(**只能 approve/confirm**)。
        if self.llm_enabled and self._is_ambiguous(obs):
            return self._llm_arbitrate(obs, hyps, ev)
        # 明显安全 / 未启用 LLM → 规则批准(rationale 与原守门人逐字一致)
        return ReasonedVerdict(
            "approve", f"财务批准:{obs['category']} 预算 {obs['amount']} 在规则内",
            ev, hyps, "rules")

    def _large_threshold(self) -> float:
        for rule in getattr(self.policy, "rules", []):
            if rule.id == "large_purchase_check":
                return float(rule.threshold or 0)
        return 0.0

    def _is_ambiguous(self, obs: dict) -> bool:
        thr = self._large_threshold()
        if thr <= 0 or not obs.get("is_expense"):
            return False
        return self.AMBIGUITY_BAND * thr <= obs["amount"] < thr   # 逼近阈值但未触发规则

    def _llm_arbitrate(self, obs: dict, hyps: list, ev: list) -> ReasonedVerdict:
        """【安全不变量】只在"规则已批准"的模糊情形被调;**只能返回 approve 或 confirm**,
        绝不 reject、绝不覆盖规则;出错/超时/响应不明一律 **fail-safe → confirm**(转人工)。"""
        try:
            ev_text = "\n".join(f"- {e.factor}: {e.detail}" for e in ev) or "(无)"
            system = ("你是财务风控助手。对一笔**规则已初步通过、但金额逼近确认阈值**的支出,"
                      "结合证据判断是否仍建议人工确认。只能回答 APPROVE(无需确认)或 CONFIRM(建议确认)。保守优先。")
            user = (f"支出:类别 {obs['category']}，金额 {obs['amount']}，对手方 {obs.get('entity','')}。\n"
                    f"证据:\n{ev_text}\n\n只回答一个词:APPROVE 或 CONFIRM。")
            resp = (self.llm.complete(system, [{"role": "user", "content": user}],
                                      max_tokens=16) or "").upper()
        except Exception:
            return ReasonedVerdict("confirm", "LLM 裁决不可用,保守转确认(fail-safe)",
                                   ev, hyps, "llm_failsafe")
        if "CONFIRM" in resp:
            return ReasonedVerdict("confirm", "LLM 就证据复核,建议人工确认", ev, hyps, "llm")
        if "APPROVE" in resp:
            return ReasonedVerdict(
                "approve", f"财务批准:{obs['category']} 预算 {obs['amount']}(LLM 复核无异常)",
                ev, hyps, "llm")
        # 响应不明 → fail-safe(绝不因 LLM 含糊而自动放行)
        return ReasonedVerdict("confirm", "LLM 响应不明,保守转确认(fail-safe)",
                               ev, hyps, "llm_failsafe")
