"""
PolicyGate —— 统一准入门(v2 ① Step4)。

审计:系统有两个"门",分属不同策略域、返回不同裁决形状:
  · Harness(推理漂移:语义/工具/范围)—— 内联在 _think,返回 HarnessResult(PASS/WARN/BLOCK);
  · skill-guard(动作权限:destructive/trust/deny>ask>allow)—— 在 SkillAdapter.invoke,返回 PermissionDecision。

PolicyGate 把它们统一到**一套 Verdict 词汇(allow/warn/deny/confirm)+ 一个接口**下:
引擎实现不动(它们是策略实现),门面只做**归一化** + 动作路径的**统一 policy.decision 审计事件**。
两条路都经门面走(pass-through:底层同一次 check/evaluate、同一结果 → 行为逐字不变)。

不 import Harness/skills(避免 kernel→上层环):harness 作参数传入(鸭子类型);
动作评估器 evaluate 由 Foxclaw **注入**。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..core.governance_audit import new_decision_id


@dataclass
class Verdict:
    allowed: bool
    level: str
    reason: str = ""
    source: str = ""
    raw: Any = None
    decision_id: str = field(default="")


class PolicyGate:
    def __init__(self, spine=None, action_evaluator: Optional[Callable] = None,
                 auditor=None):
        self._spine = spine
        self._eval = action_evaluator
        self._auditor = auditor

    @property
    def auditor(self):
        """SkillAdapter 经此取审计器,把执行终态关联到裁决 decision_id(P3-4 跨层关联)。"""
        return self._auditor

    def check_reasoning(self, harness, node) -> Verdict:
        """经统一门做推理漂移准入。底层仍是那一次 harness.check(同一 HarnessResult),
        Harness 自身已 emit harness_block/warn 事件,门面**不重复 emit**。行为一致。

        【P1-2】非 allow(warn/deny)时另记一条 governance.decision(layer=harness)——
        那是**统一审计流**的新增记录,不是重复 emit 既有 harness_* 事件;PASS 高频常态不记。"""
        r = harness.check(node)
        level = "deny" if r.is_blocked else ("warn" if r.is_warned else "allow")
        v = Verdict(allowed=not r.is_blocked, level=level,
                    reason=(r.reason or ""), source="harness", raw=r,
                    decision_id=new_decision_id())
        if level != "allow":
            self._audit(v, actor=getattr(node, "agent_id", ""),
                        action=f"reasoning:{getattr(node, 'type', '')}",
                        detail={"node_id": getattr(node, "id", ""),
                                "dominant_dim": getattr(r, "dominant_dim", "")})
        return v

    def check_action(self, skill, args: dict, rules=None,
                     confirmed: bool = False, mode: str = "strict",
                     taint_level: int = 0, taint_threshold: int = 0) -> Verdict:
        """经统一门做动作权限准入。底层仍是 permission.evaluate(同一 PermissionDecision);
        归一化为 Verdict,并对**非 allow**发统一 policy.decision.* 审计事件(动作路径原本无统一事件)。

        【P3-4】每次裁决都带 decision_id(allow 也带——调用方 SkillAdapter 拿它做执行审计的
        correlation_id,把「谁批准的」与「执行结果」join 起来)。

        【污点门槛透传】taint_level/threshold 原样交给评估器。此前门面没接这两个参数,导致
        permission.evaluate 的 Stage 3.5(碰过敏感数据 → 外发/副作用须确认)只有工具路径能触发,
        技能路径永不触发。注入的评估器(permission.evaluate / sentinel.evaluate)签名都带这两个
        kwarg;默认 0 = 关闭,既有调用方行为不变。"""
        if self._eval is None:
            return Verdict(True, "allow", "无动作评估器(降级放行)", "permission", None,
                           decision_id=new_decision_id())
        d = self._eval(skill, args, rules=rules, confirmed=confirmed, mode=mode,
                       taint_level=taint_level, taint_threshold=taint_threshold)
        level = {"allow": "allow", "ask": "confirm", "deny": "deny"}.get(d.decision, "deny")
        v = Verdict(allowed=(d.decision == "allow"), level=level,
                    reason=(d.reason or ""), source="permission", raw=d,
                    decision_id=new_decision_id())
        if level != "allow":
            self._emit(v, getattr(skill, "id", ""))
            self._audit(v, actor="", action=f"action:{getattr(skill, 'id', '')}",
                        detail={"stage": getattr(d, "stage", 0),
                                "trust_downgrade": getattr(d, "trust_downgrade", False)})
        return v

    def _emit(self, v: Verdict, skill_id: str) -> None:
        if self._spine is None:
            return
        try:
            self._spine.publish(f"policy.decision.{v.level}", {
                "source": v.source, "level": v.level,
                "reason": v.reason, "skill": skill_id,
                "decision_id": v.decision_id}, source="policy_gate")
        except Exception:
            pass

    def _audit(self, v: Verdict, actor: str, action: str, detail: dict) -> None:
        """记统一治理审计流(P1-2)。审计器可空;记录失败绝不影响裁决(审计器内部已兜)。"""
        if self._auditor is None:
            return
        self._auditor.record(layer=v.source, actor=actor, action=action,
                             verdict=v.level, reason=v.reason,
                             detail=detail, decision_id=v.decision_id)
