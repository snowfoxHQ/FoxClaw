"""
Foxclaw 财务子系统 — 结构化证据链 (Evidence Chain)

借鉴 2026 年前沿金融 AI 的"可解释性铁律":一个只给原始分数(如 0.87)
而无人类可读证据链的决策,被认为比没有 AI 更糟,因为无法被审计员复核。

每个财务风险决策输出结构化的:
  风险分数 + 风险因子(每个带证据和权重)+ 缓解因素 + 决策理由 + 建议动作 + 置信度

这与 Foxclaw 的 ThoughtChain 审计链天然契合 —— 证据链可直接沉淀为审计节点。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RiskFactor:
    """单个风险因子:是什么、证据、权重。"""
    factor: str
    evidence: str
    weight: float

    def to_dict(self) -> dict:
        return {"factor": self.factor, "evidence": self.evidence,
                "weight": round(self.weight, 3)}


@dataclass
class EvidenceChain:
    """结构化决策证据链 —— 财务决策的可审计输出。"""
    decision_type: str
    risk_score: float = 0.0
    risk_factors: list = field(default_factory=list)
    mitigating_factors: list = field(default_factory=list)
    rationale: str = ""
    recommended_action: str = ""
    confidence: float = 0.0
    additional_checks: list = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def add_factor(self, factor: str, evidence: str, weight: float):
        self.risk_factors.append(RiskFactor(factor, evidence, weight))
        return self

    def compute_risk(self) -> float:
        """综合风险:饱和式聚合(避免多个小因子简单累加成假高危)。

        取最大单因子权重为基准 + 其余因子的衰减贡献,再减缓解,裁剪 [0,1]。
        这样"1个严重"比"3个轻微"风险更高,符合直觉。
        """
        if not self.risk_factors:
            self.risk_score = 0.0
            return 0.0
        weights = sorted((f.weight for f in self.risk_factors), reverse=True)
        score = weights[0]
        for w in weights[1:]:
            score += w * (1 - score) * 0.5
        mitigation = min(0.2, 0.1 * len(self.mitigating_factors))
        self.risk_score = max(0.0, min(1.0, round(score - mitigation, 3)))
        return self.risk_score

    def level(self) -> str:
        """风险等级(供人快速判断)。"""
        s = self.risk_score
        return "high" if s >= 0.7 else "medium" if s >= 0.4 else "low"

    def to_dict(self) -> dict:
        return {
            "decision_type": self.decision_type,
            "risk_score": self.risk_score,
            "risk_level": self.level(),
            "risk_factors": [f.to_dict() for f in self.risk_factors],
            "mitigating_factors": list(self.mitigating_factors),
            "rationale": self.rationale,
            "recommended_action": self.recommended_action,
            "confidence": round(self.confidence, 3),
            "additional_checks": list(self.additional_checks),
            "created_at": self.created_at,
        }

    def to_thought_note(self) -> str:
        """压缩成一句可塞进 ThoughtChain 的审计摘要。"""
        factors = "; ".join(f"{f.factor}({f.weight})" for f in self.risk_factors[:3])
        return (f"[财务决策·{self.decision_type}] 风险={self.risk_score}({self.level()}) "
                f"| 因子: {factors} | 建议: {self.recommended_action}")


class FinanceExplainer:
    """把财务原始判断转成结构化证据链。"""

    @staticmethod
    def explain_budget_risk(budget_status: list) -> EvidenceChain:
        """预算风险 → 证据链。budget_status 为 budget_status() 的输出。"""
        ec = EvidenceChain(decision_type="budget_risk", confidence=0.9)
        over_budgets = []
        near_budgets = []
        for b in budget_status:
            ratio = b.get("usage_ratio", 0)
            cat = b.get("category", "?")
            if ratio >= 1.0:
                over = b.get("spent", 0) - b.get("monthly_limit", 0)
                w = min(0.8, 0.45 + (ratio - 1.0) * 0.35)
                ec.add_factor("超预算", f"{cat} 已用 {ratio*100:.0f}%,超支 ¥{over:.0f}",
                              weight=w)
                over_budgets.append(cat)
            elif ratio >= 0.8:
                ec.add_factor("接近预算上限",
                              f"{cat} 已用 {ratio*100:.0f}%,接近上限", weight=0.25)
                near_budgets.append(cat)
        healthy = [b.get("category") for b in budget_status
                   if b.get("usage_ratio", 0) < 0.8]
        if healthy:
            ec.mitigating_factors.append(f"{len(healthy)} 个分类预算健康")
        ec.compute_risk()
        if over_budgets:
            ec.rationale = f"{','.join(over_budgets)} 已超预算,需立即关注"
            ec.recommended_action = f"削减 {over_budgets[0]} 支出或调整预算"
            ec.additional_checks.append("检查是否有可取消的重复订阅")
        elif near_budgets:
            ec.rationale = f"{','.join(near_budgets)} 接近上限,建议放缓"
            ec.recommended_action = "本月剩余时间控制相关分类支出"
        else:
            ec.rationale = "所有预算分类处于健康水平"
            ec.recommended_action = "保持当前消费节奏"
        return ec

    @staticmethod
    def explain_anomalies(anomalies: list) -> EvidenceChain:
        """异常消费 → 证据链。anomalies 为 detect_anomalies() 的输出。"""
        ec = EvidenceChain(decision_type="anomaly", confidence=0.85)
        for a in anomalies[:5]:
            z = a.get("z_score", 0)
            w = min(0.6, 0.15 + z * 0.05)
            ec.add_factor("异常大额支出",
                          f"{a.get('entity','?')} ¥{abs(a.get('amount',0)):.0f}"
                          f"({a.get('category','?')},偏离度 z={z})", weight=w)
        ec.compute_risk()
        if anomalies:
            ec.rationale = f"检测到 {len(anomalies)} 笔异常支出,偏离历史模式"
            ec.recommended_action = "核对这些交易是否本人操作,排查盗刷/误记"
            ec.additional_checks.append("与银行流水对账确认")
            if len(anomalies) >= 3:
                ec.additional_checks.append("考虑是否近期消费习惯发生变化")
        else:
            ec.rationale = "未发现异常支出,消费模式稳定"
            ec.recommended_action = "无需处理"
            ec.confidence = 0.9
        return ec
