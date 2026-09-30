"""
PolicyGate —— 统一准入门(v2 ① Step4)。

审计:系统有两个"门",分属不同策略域、返回不同裁决形状:
  · Harness(推理漂移:语义/工具/范围)—— 内联在 _think,返回 HarnessResult(PASS/WARN/BLOCK);
  · skill-guard(动作权限:destructive/trust/deny>ask>allow)—— 在 SkillAdapter.invoke,返回 PermissionDecision。

PolicyGate 把它们统一到**一套 Verdict 词汇(allow/warn/deny/confirm)+ 一个接口**下:
引擎实现不动(它们是策略实现),门面只做**归一化** + 动作路径的**统一 policy.decision 审计事件**。
两条路都经门面走(pass-through:底层同一次 check/evaluate、同一结果 → 行为逐字不变)。

不 import Harness/skills(避免 kernel→上层环):harness 作参数传入(鸭子类型);
动作评估器 evaluate 由 HestiaOS **注入**。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class Verdict:
    allowed: bool
    level: str                 # allow | warn | deny | confirm
    reason: str = ""
    source: str = ""           # harness | permission
    raw: Any = None            # 原始 HarnessResult / PermissionDecision(调用方按需取用)


class PolicyGate:
    def __init__(self, spine=None, action_evaluator: Optional[Callable] = None):
        self._spine = spine
        self._eval = action_evaluator   # skills.permission.evaluate,注入(避免 kernel→skills 环)

    # ── 推理准入(Harness)──────────────────────────────────────
    def check_reasoning(self, harness, node) -> Verdict:
        """经统一门做推理漂移准入。底层仍是那一次 harness.check(同一 HarnessResult),
        Harness 自身已 emit harness_block/warn 事件,门面**不重复 emit**。行为一致。"""
        r = harness.check(node)
        level = "deny" if r.is_blocked else ("warn" if r.is_warned else "allow")
        return Verdict(allowed=not r.is_blocked, level=level,
                       reason=(r.reason or ""), source="harness", raw=r)

    # ── 动作准入(skill-guard)──────────────────────────────────
    def check_action(self, skill, args: dict, rules=None,
                     confirmed: bool = False, mode: str = "strict") -> Verdict:
        """经统一门做动作权限准入。底层仍是 permission.evaluate(同一 PermissionDecision);
        归一化为 Verdict,并对**非 allow**发统一 policy.decision.* 审计事件(动作路径原本无统一事件)。"""
        if self._eval is None:
            return Verdict(True, "allow", "无动作评估器(降级放行)", "permission", None)
        d = self._eval(skill, args, rules=rules, confirmed=confirmed, mode=mode)
        level = {"allow": "allow", "ask": "confirm", "deny": "deny"}.get(d.decision, "deny")
        v = Verdict(allowed=(d.decision == "allow"), level=level,
                    reason=(d.reason or ""), source="permission", raw=d)
        if level != "allow":
            self._emit(v, getattr(skill, "id", ""))
        return v

    def _emit(self, v: Verdict, skill_id: str) -> None:
        if self._spine is None:
            return
        try:
            self._spine.publish(f"policy.decision.{v.level}", {
                "source": v.source, "level": v.level,
                "reason": v.reason, "skill": skill_id}, source="policy_gate")
        except Exception:
            pass
