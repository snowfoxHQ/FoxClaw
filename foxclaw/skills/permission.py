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
import contextvars
import os
from dataclasses import dataclass, field
from typing import Literal, Optional


Decision = Literal["allow", "ask", "deny"]

# 动作三分类(A1):tier 标注的是「命中规则所属的档」,不是本次裁决结果——
# explicit 档动作确认后放行,tier 仍是 explicit(审计可查「这是需要批准的那类动作」)。
TIER_PROHIBITED = "prohibited"
TIER_EXPLICIT = "explicit"
TIER_REGULAR = "regular"

TRUSTED_ENV = "FOXCLAW_TRUSTED_MODE"


def trusted_mode_enabled() -> bool:
    """trusted 模式是否被显式授权(env FOXCLAW_TRUSTED_MODE=1)。

    【安全整改】trusted 会跳过 Stage 4 的 ask 规则/external/副作用确认,
    不能由调用方单方面声明——必须宿主显式开闸(与 FOXCLAW_MCP_AGENTS 同款设计),
    否则一律降级为 strict。

    【A2 重构】trusted 不再是布尔全放行:仅当例外规则(foxclaw.skills.exceptions)
    的全部 AND 条件满足才豁免 Stage 4;无例外命中 → 落回常规评估。deny 与
    destructive 确认在例外判定之前,不可被任何例外豁免。
    """
    return os.environ.get(TRUSTED_ENV, "0").strip().lower() in ("1", "true", "yes", "on")


# A2:评估上下文(请求来源信号),由调用方(认知管线)在评估前设置。
# 例外条件(如「请求来自用户聊天」)从这读;无上下文 = 条件不成立(fail-closed)。
_EVAL_CONTEXT: contextvars.ContextVar[dict] = contextvars.ContextVar("foxclaw_eval_context", default=None)


def set_eval_context(ctx: dict):
    """设置当前评估上下文(请求来源等),返回 token 供 reset。"""
    return _EVAL_CONTEXT.set(dict(ctx or {}))


def clear_eval_context(token):
    _EVAL_CONTEXT.reset(token)


def eval_context() -> dict:
    return _EVAL_CONTEXT.get() or {}

MISSING_PARAMS = "MISSING_PARAMS"
DESTRUCTIVE_CONFIRM = "DESTRUCTIVE_CONFIRM"
SIDE_EFFECT_CONFIRM = "SIDE_EFFECT_CONFIRM"
ASK_RULE = "ASK_RULE"
EXTERNAL_CONFIRM = "EXTERNAL_CONFIRM"
TAINT_CONFIRM = "TAINT_CONFIRM"
ORIGIN_APPROVED = "ORIGIN_APPROVED"


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
    tier: str = ""

    def to_dict(self) -> dict:
        return {
            "decision": self.decision, "stage": self.stage, "reason": self.reason,
            "trust_downgrade": self.trust_downgrade,
            "requires_confirmation": self.requires_confirmation,
            "reason_code": self.reason_code,
            "tier": self.tier,
        }


def evaluate(skill, args: dict, rules: Optional[PermissionRules] = None,
             confirmed: bool = False, mode: str = "strict",
             taint_level: int = 0, taint_threshold: int = 0,
             pre_approved: bool = False) -> PermissionDecision:
    """四阶段权限管线。返回 allow / ask / deny。

    skill: SkillSpec(需有 id / side_effect / kind,及可选 destructive/external/read_only)

    【P-Muse·污点门槛】taint_level = 本会话已取回数据的最高敏感度(见
    core.injection_guard.taint_level);taint_threshold = 触发二次确认的阈值。
    两者默认 0 = 关闭,既有调用方行为逐字不变。仅当「污点达标 且 动作为外发/副作用」时,
    在 Stage 3 强制升级为 ask(TAINT_CONFIRM)——对齐 Muse「碰过敏感数据 → 动态提门槛」。

    【A3·原始请求即批准】pre_approved=True 表示该动作已被用户原始请求明确包含
    (由调用方确定性判定:用户发起 + 请求文本点名该动作 + 非 pay/destructive),
    Stage 4 的常规确认免除,不重复问一遍已经说过的事。仅作用于 Stage 4;
    deny / destructive / 污点门槛在更早阶段,不受影响。pay 一律仍需确认。
    """
    rules = rules or PermissionRules()
    sid = skill.id

    # 【安全整改】trusted 需宿主显式授权(FOXCLAW_TRUSTED_MODE=1),否则降级 strict——
    # 防止任意调用方传 mode="trusted" 就跳过 Stage 4 确认。
    if mode == "trusted" and not trusted_mode_enabled():
        mode = "strict"

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
        return PermissionDecision("deny", 2, reason=f"命中 deny 规则(不可覆盖): {sid}",
                                  tier=TIER_PROHIBITED)
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
            reason="readonly 模式下禁止非只读操作", trust_downgrade=downgrade,
            tier=TIER_PROHIBITED)

    if destructive and not confirmed:
        return PermissionDecision("ask", 3,
            reason="不可逆(destructive)操作,必须用户确认",
            trust_downgrade=downgrade, requires_confirmation=True,
            reason_code=DESTRUCTIVE_CONFIRM, tier=TIER_EXPLICIT)

    if taint_threshold and taint_level >= taint_threshold and not confirmed:
        if side_effect in ("write", "send", "pay") or external:
            return PermissionDecision("ask", 3,
                reason="会话已取回用户敏感数据,外发/副作用动作需二次确认(污点防护)",
                trust_downgrade=downgrade, requires_confirmation=True,
                reason_code=TAINT_CONFIRM, tier=TIER_EXPLICIT)

    if mode == "trusted":
        # A2:trusted 不再布尔放行——例外规则全部条件满足才豁免 Stage 4;
        # 无例外命中 → 落回常规评估(该 ask 还 ask),与主规则同一管线,无旁路。
        from .exceptions import evaluate_trusted_exception
        ok, why = evaluate_trusted_exception(args, eval_context())
        if ok:
            return PermissionDecision("allow", 3,
                reason=f"trusted 例外豁免({why})",
                trust_downgrade=downgrade, tier=TIER_REGULAR)
        # 未豁免:继续走 Stage 4 常规评估

    needs_confirm = (
        ask_by_rule or external or side_effect in ("write", "send", "pay")
    )
    # A3:原始请求已明确包含 → Stage 4 常规确认免除;pay 除外(资金动作一律问)
    if needs_confirm and not confirmed and pre_approved and side_effect != "pay":
        return PermissionDecision("allow", 4,
            reason="原始请求已明确包含该动作(用户发起),免重复确认",
            trust_downgrade=downgrade, reason_code=ORIGIN_APPROVED,
            tier=TIER_EXPLICIT)
    if needs_confirm and not confirmed:
        why = ("命中 ask 规则" if ask_by_rule else
               "外部调用" if external else f"{side_effect} 副作用")
        code = (ASK_RULE if ask_by_rule else
                EXTERNAL_CONFIRM if external else SIDE_EFFECT_CONFIRM)
        return PermissionDecision("ask", 4,
            reason=f"需确认: {why}", trust_downgrade=downgrade,
            requires_confirmation=True, reason_code=code, tier=TIER_EXPLICIT)

    reason = ("命中 allow 规则" if allow_by_rule else
              "只读无副作用" if read_only else "已确认")
    # 显式批准档动作确认后放行,tier 仍标 explicit(档随规则走,不随结果走)
    explicit_hit = (destructive or ask_by_rule or external
                    or side_effect in ("write", "send", "pay"))
    tier = TIER_EXPLICIT if explicit_hit else TIER_REGULAR
    return PermissionDecision("allow", 4, reason=reason, trust_downgrade=downgrade,
                              tier=tier)
