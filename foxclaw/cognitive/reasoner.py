"""
DomainReasoner —— 领域认知化的推理骨架(v2 ③)。

把领域裁决从"规则引擎"升级为显式的认知管线:
    观察(observe) → 假设(hypothesize) → 收集证据(gather_evidence) → 裁决(arbitrate)

**双层保留(铁律)**:规则永远是明确/硬约束情形的权威 + LLM/推理只增强模糊情形;
每个裁决都带**证据链**(可审计);LLM 出错一律 **fail-safe**(转确认,呼应 P3 教训)。
Step1 只做形式化:用现有规则实现本接口,裁决逐字不变、显式产出证据链。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Evidence:
    factor: str
    detail: str = ""
    weight: float = 1.0
    raw: Any = None

    def to_dict(self) -> dict:
        return {"factor": self.factor, "detail": self.detail, "weight": self.weight}


@dataclass
class ReasonedVerdict:
    decision: str
    rationale: str
    evidence: list = field(default_factory=list)
    hypotheses: list = field(default_factory=list)
    source: str = "rules"

    def to_dict(self) -> dict:
        return {"decision": self.decision, "rationale": self.rationale,
                "source": self.source, "hypotheses": list(self.hypotheses),
                "evidence": [e.to_dict() for e in self.evidence]}


class DomainReasoner:
    """领域推理器基座:reason() 串起四步。子类覆盖各步(默认空/放行)。"""

    def observe(self, subject) -> dict:
        return {}

    def hypothesize(self, observation: dict) -> list:
        return []

    def gather_evidence(self, subject, observation: dict) -> list:
        return []

    def arbitrate(self, subject, observation: dict,
                  hypotheses: list, evidence: list) -> ReasonedVerdict:
        return ReasonedVerdict("approve", "无特殊裁决", evidence, hypotheses, "rules")

    def reason(self, subject) -> ReasonedVerdict:
        """观察 → 假设 → 收集证据 → 裁决。返回带证据链的 ReasonedVerdict。"""
        obs = self.observe(subject)
        hyps = self.hypothesize(obs)
        ev = self.gather_evidence(subject, obs)
        return self.arbitrate(subject, obs, hyps, ev)

    @staticmethod
    def llm_available(llm, enabled: bool) -> bool:
        """LLM 是否真正可用:启用 + 非空 + 非 stub(无真实模型时视为不可用)。"""
        return bool(enabled) and llm is not None and not getattr(llm, "is_stub", True)

    @staticmethod
    def llm_should_escalate(llm, system: str, user: str) -> bool:
        """让 LLM 就"规则未发现硬问题、但情形微妙"的情况投票是否升级为人工确认。
        **结构上只增强、不推翻**:只返回 True(建议 confirm)/False(维持规则裁决),
        无从产生"推翻规则"的结果。出错/超时/响应不明 → False(维持规则裁决;
        顾问域无治理闸口,不因 LLM 抖动制造确认噪声——与守门域 finance 的
        error→confirm 区别正在于此)。"""
        try:
            resp = (llm.complete(system, [{"role": "user", "content": user}],
                                 max_tokens=16) or "").upper()
        except Exception:
            return False
        return "CONFIRM" in resp or "ESCALATE" in resp
