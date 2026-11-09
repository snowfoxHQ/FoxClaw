"""
Foxclaw 主管编排子系统 — 全局态势 + 跨 Agent 编排
主管不是领域专家,而是协调者。它的两个核心能力:
  1. GlobalSituation:汇总三个内核状态,形成"驾驶舱视图"
  2. CrossAgentRequest:一个 Agent 的行为触发另一个 Agent 的约束
     (典型:行程 Agent 规划出差 → 向财务 Agent 申请差旅预算 → 财务守门人审批)
"""

from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Literal
import uuid


RequestType = Literal["budget_request", "schedule_request", "knowledge_request",
                      "info_request"]
RequestStatus = Literal["pending", "approved", "rejected", "needs_confirmation"]


@dataclass
class CrossAgentRequest:
    """跨 Agent 请求 — 编排的基本单元。
    一个 Agent 向另一个发起请求,目标 Agent 用自己的守门人裁决。"""
    from_agent: str
    to_agent: str
    request_type: RequestType
    payload: dict
    reason: str = ""
    status: RequestStatus = "pending"
    verdict: str = ""
    negotiation: list = field(default_factory=list)
    id: str = field(default_factory=lambda: f"req_{uuid.uuid4().hex[:10]}")
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GlobalSituation:
    """全局态势 — 主管的驾驶舱视图,汇总各内核关键指标。"""
    finance: dict = field(default_factory=dict)
    learning: dict = field(default_factory=dict)
    itinerary: dict = field(default_factory=dict)
    alerts: list = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


class Orchestrator:
    """主管编排引擎:汇总态势 + 路由跨 Agent 请求到目标守门人。

    【P1-3】不再直接持有 agents 字典,而是持 CapabilityRegistry、按需惰性解析
    (打断 Supervisor⇄Orchestrator 直接环:主管持有本引擎,本引擎经注册表解析 agents,
    不再反向直接持有含主管的 agents 字典)。"""

    def __init__(self, caps, auditor=None):
        self._caps = caps
        self._auditor = auditor
        self.request_log: deque = deque(maxlen=1000)

    @property
    def agents(self) -> dict:
        """按名字从注册中枢解析 agents(id -> agent 实例)。"""
        return self._caps.get("agents") or {}

    def gather_situation(self) -> GlobalSituation:
        """通用聚合各 Agent 自报的领域态势(agent.situation()),形成驾驶舱视图。
        编排层不再直接读 finance/learning/itinerary 内核——领域知识已归属各领域 Agent。"""
        sit = GlobalSituation()
        alerts: list[dict] = []
        for agent in self.agents.values():
            try:
                s = agent.situation()
            except Exception:
                continue
            if not s:
                continue
            doms = s.get("domains")
            if isinstance(doms, dict) and doms:
                for d, st in doms.items():
                    if d and hasattr(sit, d):
                        setattr(sit, d, st or {})
            else:
                domain = s.get("domain")
                if domain and hasattr(sit, domain):
                    setattr(sit, domain, s.get("status", {}) or {})
            alerts += s.get("alerts", []) or []
        sit.alerts = sorted(alerts,
                            key=lambda a: {"critical": 0, "warn": 1, "info": 2}
                            .get(a.get("severity"), 3))
        return sit

    def route_request(self, req: CrossAgentRequest) -> CrossAgentRequest:
        """把请求路由给目标 Agent,由**目标 Agent**用自己的守门人裁决(编排层不懂领域)。"""
        target = self.agents.get(req.to_agent)
        if target is None:
            req.status = "needs_confirmation"
            req.verdict = f"目标 Agent '{req.to_agent}' 不存在,无法裁决,转人工确认"
        else:
            try:
                target.handle_cross_request(req)
            except Exception as e:
                req.status = "needs_confirmation"
                req.verdict = f"裁决异常,转人工确认: {e}"
        self.request_log.append(req)
        self._audit_verdict(req)
        return req

    def _audit_verdict(self, req: CrossAgentRequest) -> None:
        """【P1-3】把本次裁决记入统一治理审计流(governance.decision)。

        correlation_id=req.id:negotiate 的多轮裁决共用同一 req.id ⇒ 协商链可 join;
        每次裁决的 decision_id 各自独立(由审计器生成)。审计失败绝不影响路由。"""
        if self._auditor is None:
            return
        verdict = {"approved": "allow", "rejected": "deny",
                   "needs_confirmation": "confirm"}.get(req.status, req.status)
        self._auditor.record(
            layer="orchestration", actor=req.from_agent,
            action=f"cross_request:{req.request_type}->{req.to_agent}",
            verdict=verdict, reason=req.verdict,
            detail={"status": req.status,
                    "negotiation_round": len(req.negotiation)},
            correlation_id=req.id)

    def negotiate(self, req: CrossAgentRequest, max_rounds: int = 3) -> CrossAgentRequest:
        """跑协商协议。原先裁决是**终局**:财务说预算不足→rejected→到此为止,用户只拿到
        一句"被拒绝"。现在:发起方可拿着拒绝理由降标准重来(改高铁/换酒店/减一天)。

        职责边界(守 P3):**编排层只跑协议、不懂领域** ——
          · 裁决权仍在**目标 Agent**的守门人(route_request 原样调用,不绕过)
          · 让步逻辑仍在**发起 Agent**(agent.concede),编排层只问"你能让吗"

        安全不变量:
          · **有界**:最多 max_rounds 轮,防死循环
          · **必须真让步**:payload 没变化就停(否则无限重复同一请求)
          · **协商失败维持 rejected,绝不降级成 needs_confirmation** ——
            那等于给用户开一个点穿守门人的口子。协商只能"找到守门人愿意批的方案",
            不能"把不批变成可以批"。
        """
        for rnd in range(1, max(1, max_rounds) + 1):
            self.route_request(req)
            req.negotiation.append({"round": rnd, "status": req.status,
                                    "verdict": req.verdict,
                                    "payload": dict(req.payload)})
            if req.status != "rejected":
                break
            requester = self.agents.get(req.from_agent)
            if requester is None:
                break
            try:
                revised = requester.concede(req, req.verdict)
            except Exception as e:
                req.negotiation.append({"round": rnd, "concede_error": str(e)[:120]})
                break
            if not revised or revised == req.payload:
                break
            req.negotiation[-1]["conceded_to"] = dict(revised)
            req.payload = revised
        return req


__all__ = ["CrossAgentRequest", "GlobalSituation", "Orchestrator"]
