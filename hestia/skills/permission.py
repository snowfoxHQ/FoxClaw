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


@dataclass
class PermissionRules:
    """宿主提供的规则集。deny 优先级最高且不可覆盖。"""
    deny: list = field(default_factory=list)    # skill id / glob,命中即拒绝
    ask: list = field(default_factory=list)     # 命中需确认
    allow: list = field(default_factory=list)   # 命中放行

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
    stage: int                  # 哪个阶段做出的决定(1-4)
    reason: str = ""
    trust_downgrade: bool = False   # 结果是否须按「不可信」处理(MCP/远程)
    requires_confirmation: bool = False

    def to_dict(self) -> dict:
        return {
            "decision": self.decision, "stage": self.stage, "reason": self.reason,
            "trust_downgrade": self.trust_downgrade,
            "requires_confirmation": self.requires_confirmation,
        }


def evaluate(skill, args: dict, rules: Optional[PermissionRules] = None,
             confirmed: bool = False, mode: str = "strict") -> PermissionDecision:
    """四阶段权限管线。返回 allow / ask / deny。

    skill: SkillSpec(需有 id / side_effect / kind,及可选 destructive/external/read_only)
    """
    rules = rules or PermissionRules()
    sid = skill.id

    # ---------- Stage 1: validate(输入合法性)----------
    # 参数 schema 基本校验:声明的必填参数是否齐全
    # 兼容两种 params 格式:{"k": "str"}(简单)或 {"k": {"required": True}}(详细)
    params = getattr(skill, "params", {}) or {}
    missing = []
    for k, spec in params.items():
        required = isinstance(spec, dict) and spec.get("required")
        if required and k not in (args or {}):
            missing.append(k)
    if missing:
        # 校验失败 → 优雅降级为 ask(不是安全拒绝,是数据不全)
        return PermissionDecision("ask", 1,
            reason=f"缺少必填参数: {', '.join(missing)}",
            requires_confirmation=True)

    # ---------- Stage 2: rule-match(deny > ask > allow 铁律)----------
    if rules.hits_deny(sid):
        return PermissionDecision("deny", 2, reason=f"命中 deny 规则(不可覆盖): {sid}")
    ask_by_rule = rules.hits_ask(sid)
    allow_by_rule = rules.hits_allow(sid)
    # deny 不命中时,ask 优先于 allow

    # ---------- Stage 3: capability(能力检查,Fail-closed)----------
    side_effect = getattr(skill, "side_effect", "read")
    destructive = getattr(skill, "destructive", False)
    external = getattr(skill, "external", False)
    # read_only 字段默认 None → 按 side_effect 推断(read 即只读);显式 True/False 则尊重
    _ro = getattr(skill, "read_only", None)
    read_only = (side_effect == "read") if _ro is None else _ro
    # 单一真源(P6 修复):信任分级由 SkillSpec.trust_level() 唯一裁定。此前 permission 另存一份
    # TRUST_LEVEL dict,与 trust_level() 重复且含死 key(bundled/remote —— kind 永不取此二值);对 3 个
    # 合法 SkillKind(local/web_api/mcp)二者 downgrade 行为完全一致,故收敛为单一真源、行为不变。
    # duck-typed skill 无 trust_level 方法时 fail-closed 当低信任。
    _tl = getattr(skill, "trust_level", None)
    trust = _tl() if callable(_tl) else "low"
    downgrade = trust == "low"   # 低信任(mcp/远程)结果须按不可信处理

    # readonly 模式:非只读一律拒绝
    if mode == "readonly" and not read_only:
        return PermissionDecision("deny", 3,
            reason="readonly 模式下禁止非只读操作", trust_downgrade=downgrade)

    # destructive 永不自动放行(即使有 allow 规则也要确认)
    if destructive and not confirmed:
        return PermissionDecision("ask", 3,
            reason="不可逆(destructive)操作,必须用户确认",
            trust_downgrade=downgrade, requires_confirmation=True)

    # trusted 模式:跳过 ask,只保留 deny(上面已查)+ destructive(上面已查)
    if mode == "trusted":
        return PermissionDecision("allow", 3,
            reason="trusted 模式放行", trust_downgrade=downgrade)

    # ---------- Stage 4: confirm(确认)----------
    needs_confirm = (
        ask_by_rule or external or side_effect in ("write", "send", "pay")
    )
    if needs_confirm and not confirmed:
        why = ("命中 ask 规则" if ask_by_rule else
               "外部调用" if external else f"{side_effect} 副作用")
        return PermissionDecision("ask", 4,
            reason=f"需确认: {why}", trust_downgrade=downgrade,
            requires_confirmation=True)

    # 放行:显式 allow 规则,或只读无副作用,或已确认
    reason = ("命中 allow 规则" if allow_by_rule else
              "只读无副作用" if read_only else "已确认")
    return PermissionDecision("allow", 4, reason=reason, trust_downgrade=downgrade)
