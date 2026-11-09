"""
LearningReviewReasoner —— 学习复习推理的认知化(v2 ③ Step4)。

把 SpacedRepetition.forgetting_risk / learning_load 的规则裁决,形式化为
    观察 → 假设 → 证据 → 裁决,
**规则永远权威**(存在高遗忘风险知识点 → confirm=建议立即复习,绝不被 LLM 推翻);
可选 LLM 层(默认关)只对"无高风险、但负荷偏重"的微妙情形**升级** approve→confirm。
裁决带证据链(可审计)。顾问域(非治理闸口):LLM 出错→维持规则裁决。
"""
from __future__ import annotations
from ..cognitive.reasoner import DomainReasoner, ReasonedVerdict, Evidence
from .spaced_repetition import SpacedRepetition

HEAVY_DAILY = 20.0


class LearningReviewReasoner(DomainReasoner):
    """subject = states(list[ReviewState])。裁决:approve(学习状态健康) / confirm(需干预)。"""

    def __init__(self, horizon_days: int = 3, llm=None, llm_enabled: bool = False):
        self.horizon_days = horizon_days
        self.llm = llm
        self.llm_enabled = llm_enabled

    def observe(self, states) -> dict:
        load = SpacedRepetition.learning_load(states)
        return {"total": len(states), "overdue": load["overdue"],
                "daily_avg": load["daily_avg"], "total_due": load["total_due"]}

    def hypothesize(self, obs: dict) -> list:
        h = []
        if obs["overdue"]:
            h.append("可能有逾期未复习")
        if obs["daily_avg"] >= HEAVY_DAILY:
            h.append("可能复习负荷过重")
        return h or ["学习状态可能健康"]

    def gather_evidence(self, states, obs: dict) -> list:
        risky = SpacedRepetition.forgetting_risk(states, self.horizon_days)
        ev = [Evidence(factor="forgetting_risk",
                       detail=r["reason"] + f"(item {r['item_id']})", weight=1.0, raw=r)
              for r in risky]
        if not risky:
            ev.append(Evidence("no_urgent_risk", "无即将到期且掌握度低的知识点", 0.5))
        ev.append(Evidence("learning_load",
                           f"未来窗口共 {obs['total_due']} 项待复习,日均 {obs['daily_avg']}", 0.3))
        return ev

    def arbitrate(self, states, obs, hyps, ev) -> ReasonedVerdict:
        triggered = [e for e in ev if e.raw is not None]
        if triggered:
            top = triggered[:3]
            return ReasonedVerdict(
                "confirm", f"发现 {len(triggered)} 个高遗忘风险知识点,建议优先复习:"
                + "；".join(e.detail for e in top),
                ev, hyps, "rules")
        if DomainReasoner.llm_available(self.llm, self.llm_enabled) and obs["daily_avg"] >= HEAVY_DAILY:
            if DomainReasoner.llm_should_escalate(self.llm, *self._llm_prompt(obs, ev)):
                return ReasonedVerdict("confirm", "复习负荷偏重,LLM 建议调整计划", ev, hyps, "llm")
        return ReasonedVerdict("approve", "学习状态健康:无高遗忘风险知识点", ev, hyps, "rules")

    def _llm_prompt(self, obs, ev):
        ev_text = "\n".join(f"- {e.factor}: {e.detail}" for e in ev) or "(无)"
        system = ("你是学习助手。当前无高遗忘风险知识点,但复习负荷偏重。"
                  "判断是否建议用户调整复习计划。只回答 CONFIRM(建议调整)或 OK(无需)。")
        user = f"负荷:日均 {obs['daily_avg']} 项。\n证据:\n{ev_text}\n\n只回答一个词:CONFIRM 或 OK。"
        return system, user
