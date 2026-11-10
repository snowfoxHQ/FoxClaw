"""
工具权限管线 (Permission Pipeline) —— HHS 标准 skill-guard 的代码落地。

把抽象的「四阶段权限管线」实现为可运行代码:
  Stage 1 validate    —— 输入合法性(参数 schema)
  Stage 2 rule-match  —— deny > ask > allow 优先级铁律
  Stage 3 capability  —— read_only / destructive / external,Fail-closed
  Stage 4 confirm      —— 有副作用/危险操作需确认

对应 docs/internal/HARNESS_STANDARD.md 的 A 类工具权限护栏。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, Optional


Decision = Literal["allow", "ask", "deny"]

MISSING_PARAMS = "MISSING_PARAMS"
DESTRUCTIVE_CONFIRM = "DESTRUCTIVE_CONFIRM"
SIDE_EFFECT_CONFIRM = "SIDE_EFFECT_CONFIRM"
ASK_RULE = "ASK_RULE"
EXTERNAL_CONFIRM = "EXTERNAL_CONFIRM"
TAINT_CONFIRM = "TAINT_CONFIRM"


@dataclass
class PermissionRules:
    """宿主提供的规则集。deny 优先级最高且不可覆盖。"""
    deny: list = field(default_factory=list)
    ask: list = field(default_factory=list)
    allow: list = field(default_factory=list)

    @staticmethod
    def _match(patterns: list, skill_id: str) -> bool:
        import fnmatch
        return any(fnmatch.fnmatch(skill_id, p) for p in patterns)

    def hits_deny(self, sid: str) -> bool:
        return self._match(self.deny, sid)

    def hits_ask(self, sid: str) -> bool:
        return self._match(self.ask, sid)

    def hits_allow(self, sid: str) -> bool:
        return self._match(self.allow, sid)


@dataclass
class PermissionDecision:
    decision: Decision
    stage: int
    reason: str = ""
    trust_downgrade: bool = False
    requires_confirmation: bool = False
    reason_code: str = ""

    def to_dict(self) -> dict:
        return {
            "decision": self.decision, "stage": self.stage, "reason": self.reason,
            "trust_downgrade": self.trust_downgrade,
            "requires_confirmation": self.requires_confirmation,
            "reason_code": self.reason_code,
        }


def evaluate(skill, args: dict, rules: Optional[PermissionRules] = None,
             confirmed: bool = False, mode: str = "strict",
             taint_level: int = 0, taint_threshold: int = 0) -> PermissionDecision:
    """四阶段权限管线。返回 allow / ask / deny。

    skill: SkillSpec(需有 id / side_effect / kind,及可选 destructive/external/read_only)

    【P-Muse·污点门槛】taint_level = 本会话已取回数据的最高敏感度(见
    core.injection_guard.taint_level);taint_threshold = 触发二次确认的阈值。
    两者默认 0 = 关闭,既有调用方行为逐字不变。仅当「污点达标 且 动作为外发/副作用」时,
    在 Stage 3 强制升级为 ask(TAINT_CONFIRM)——对齐 Muse「碰过敏感数据 → 动态提门槛」。
    """
    rules = rules or PermissionRules()
    sid = skill.id

    params = getattr(skill, "params", {}) or {}
    missing = []
    for k, spec in params.items():
        required = isinstance(spec, dict) and spec.get("required")
        if required and k not in (args or {}):
            missing.append(k)
    if missing:
        return PermissionDecision("ask", 1,
            reason=f"缺少必填参数: {', '.join(missing)}",
            requires_confirmation=True, reason_code=MISSING_PARAMS)

    if rules.hits_deny(sid):
        return PermissionDecision("deny", 2, reason=f"命中 deny 规则(不可覆盖): {sid}")
    ask_by_rule = rules.hits_ask(sid)
    allow_by_rule = rules.hits_allow(sid)

    side_effect = getattr(skill, "side_effect", "write")
    destructive = getattr(skill, "destructive", False)
    external = getattr(skill, "external", False)
    _ro = getattr(skill, "read_only", None)
    read_only = (side_effect == "read") if _ro is None else _ro
    _tl = getattr(skill, "trust_level", None)
    try:
        trust = _tl() if callable(_tl) else "low"
    except Exception:
        trust = "low"
    downgrade = trust == "low"

    if mode == "readonly" and not read_only:
        return PermissionDecision("deny", 3,
            reason="readonly 模式下禁止非只读操作", trust_downgrade=downgrade)

    if destructive and not confirmed:
        return PermissionDecision("ask", 3,
            reason="不可逆(destructive)操作,必须用户确认",
            trust_downgrade=downgrade, requires_confirmation=True,
            reason_code=DESTRUCTIVE_CONFIRM)

    if taint_threshold and taint_level >= taint_threshold and not confirmed:
        if side_effect in ("write", "send", "pay") or external:
            return PermissionDecision("ask", 3,
                reason="会话已取回用户敏感数据,外发/副作用动作需二次确认(污点防护)",
                trust_downgrade=downgrade, requires_confirmation=True,
                reason_code=TAINT_CONFIRM)

    if mode == "trusted":
        return PermissionDecision("allow", 3,
            reason="trusted 模式放行", trust_downgrade=downgrade)

    needs_confirm = (
        ask_by_rule or external or side_effect in ("write", "send", "pay")
    )
    if needs_confirm and not confirmed:
        why = ("命中 ask 规则" if ask_by_rule else
               "外部调用" if external else f"{side_effect} 副作用")
        code = (ASK_RULE if ask_by_rule else
                EXTERNAL_CONFIRM if external else SIDE_EFFECT_CONFIRM)
        return PermissionDecision("ask", 4,
            reason=f"需确认: {why}", trust_downgrade=downgrade,
            requires_confirmation=True, reason_code=code)

    reason = ("命中 allow 规则" if allow_by_rule else
              "只读无副作用" if read_only else "已确认")
    return PermissionDecision("allow", 4, reason=reason, trust_downgrade=downgrade)
