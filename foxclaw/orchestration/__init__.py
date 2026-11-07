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
    from_agent: str                     # 发起方
    to_agent: str                       # 目标方
    request_type: RequestType
    payload: dict                       # 请求内容(金额/时间/查询等)
    reason: str = ""
    status: RequestStatus = "pending"
    verdict: str = ""                   # 目标 Agent 的裁决说明
    negotiation: list = field(default_factory=list)   # 协商回合记录(每轮:裁决 + 让步)
    id: str = field(default_factory=lambda: f"req_{uuid.uuid4().hex[:10]}")
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GlobalSituation:
    """全局态势 — 主管的驾驶舱视图,汇总各内核关键指标。"""
    finance: dict = field(default_factory=dict)     # 超支?守门人告警?
    learning: dict = field(default_factory=dict)    # 待复习?薄弱主题?
    itinerary: dict = field(default_factory=dict)   # 行程冲突?赶不及?
    alerts: list = field(default_factory=list)      # 跨域汇总的高优先告警
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


class Orchestrator:
    """主管编排引擎:汇总态势 + 路由跨 Agent 请求到目标守门人。

    【P1-3】不再直接持有 agents 字典,而是持 CapabilityRegistry、按需惰性解析
    (打断 Supervisor⇄Orchestrator 直接环:主管持有本引擎,本引擎经注册表解析 agents,
    不再反向直接持有含主管的 agents 字典)。"""

    def __init__(self, caps, auditor=None):
        self._caps = caps               # CapabilityRegistry
        # 【P1-3】统一治理审计(可空:standalone 无审计)。裁决事件原本**完全没有**——
        # 跨 Agent 委派的批准/拒绝/转人工,事前无任何可观测记录。
        self._auditor = auditor
        # 【深审修复·长期运行泄漏】原为普通 list、只增不减且**全库从不读**——每次跨 Agent 请求
        # append 一个 req → 无界增长。改有界 deque(保留最近 N 条供未来观测,符合本库 token_meter/
        # HookManager 的有界日志惯例);deque.append 原子 ⇒ 并发路由也安全。
        self.request_log: deque = deque(maxlen=1000)

    @property
    def agents(self) -> dict:
        """按名字从注册中枢解析 agents(id -> agent 实例)。"""
        return self._caps.get("agents") or {}

    # ── 全局态势汇总(P3:通用聚合,不懂任何领域内部 API)────────────
    def gather_situation(self) -> GlobalSituation:
        """通用聚合各 Agent 自报的领域态势(agent.situation()),形成驾驶舱视图。
        编排层不再直接读 finance/learning/itinerary 内核——领域知识已归属各领域 Agent。"""
        sit = GlobalSituation()
        alerts = []
        for agent in self.agents.values():
            try:
                s = agent.situation()
            except Exception:
                continue
            if not s:
                continue
            # 一个 Agent 可以自报**多个**领域(拓扑 5→3 后,办公学习 Agent 同时覆盖
            # learning 与 itinerary),故 `domains: {name: status}` 与旧的单 `domain` 都收。
            doms = s.get("domains")
            if isinstance(doms, dict) and doms:
                for d, st in doms.items():
                    if d and hasattr(sit, d):       # finance/learning/itinerary
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

    # ── 跨 Agent 请求路由(P3:通用委托,由目标 Agent 用自己的守门人裁决)──────
    def route_request(self, req: CrossAgentRequest) -> CrossAgentRequest:
        """把请求路由给目标 Agent,由**目标 Agent**用自己的守门人裁决(编排层不懂领域)。"""
        target = self.agents.get(req.to_agent)
        if target is None:
            # 【P1-0】原为 status="approved"(默认放行)—— 那是 fail-open:发起方会拿到
            # "已批准",但**没有任何 Agent 真的处理过这个请求**。后果是实打实的,例如
            # 行程 Agent 向未注册的 finance 申请差旅预算,会直接拿到批准并据此下单。
            # 与下方「裁决异常 → 转人工确认」同属一条原则:裁决权不在编排层,凡未真正
            # 裁决过的,都不能算通过。改为 needs_confirmation 同样"不崩",只是不再说谎。
            req.status = "needs_confirmation"
            req.verdict = f"目标 Agent '{req.to_agent}' 不存在,无法裁决,转人工确认"
        else:
            try:
                target.handle_cross_request(req)
            except Exception as e:
                # 守门人裁决崩溃(如畸形 payload 让解析抛错)必须 **fail-safe**:转人工确认,
                # 绝不自动放行——对守门人而言,裁决失败时放行=fail-open,是安全隐患。
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

    # ── 协商闭环:被拒 → 发起方让步 → 再裁决 ──────────────────────────
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
                break                                  # 通过/待确认 → 协商结束
            requester = self.agents.get(req.from_agent)
            if requester is None:
                break
            try:
                revised = requester.concede(req, req.verdict)   # 问发起方:能让吗?
            except Exception as e:
                req.negotiation.append({"round": rnd, "concede_error": str(e)[:120]})
                break                                  # 让步逻辑出错 → 停,维持原裁决
            if not revised or revised == req.payload:
                break                                  # 让无可让 / 无实质变化 → 停
            req.negotiation[-1]["conceded_to"] = dict(revised)
            req.payload = revised
        return req


__all__ = ["CrossAgentRequest", "GlobalSituation", "Orchestrator"]
