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

# 【P5-1】ask 的结构化原因码。此前 ask + requires_confirmation=True 只有一个语义:
# 「用户确认后就能执行」。但 Stage 1 的「缺参数」不是「需确认」——用户确认一百次
# 也补不上参数,必须先补数据。reason_code 让调用方(前端/编排层/审计)能区分:
MISSING_PARAMS = "MISSING_PARAMS"                # 缺必填参数,不可通过确认绕过(先补数据)
DESTRUCTIVE_CONFIRM = "DESTRUCTIVE_CONFIRM"      # 不可逆操作,需确认
SIDE_EFFECT_CONFIRM = "SIDE_EFFECT_CONFIRM"      # write/send/pay 副作用,需确认
ASK_RULE = "ASK_RULE"                            # 命中宿主 ask 规则
EXTERNAL_CONFIRM = "EXTERNAL_CONFIRM"            # 外部调用,需确认
TAINT_CONFIRM = "TAINT_CONFIRM"                  # 会话已取回敏感数据,外发/副作用须二次确认
# deny / allow 侧文稿未定义 code,保守留 "":deny 的铁律语义(deny 不可覆盖)已由
# decision + stage 充分表达,不擅自扩大枚举面。


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
    # 【P5-1】ask 的结构化原因码(见模块顶部常量);非 ask 或文稿未定义的情形为 ""
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
        # 【P5-1】MISSING_PARAMS:确认无法绕过缺参数,调用方须先补数据
        return PermissionDecision("ask", 1,
            reason=f"缺少必填参数: {', '.join(missing)}",
            requires_confirmation=True, reason_code=MISSING_PARAMS)

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
    # 【P5-0】trust_level() 抛异常 → 当低信任(fail-closed),不让权限管线崩。
    # 第三方 skill 的 trust_level() 若有 bug(如 AttributeError),原实现会让异常直接冒泡、
    # evaluate 崩溃,用户看到「调用失败」而非「权限拒绝」——这是 fail-open。改为兜住异常、
    # 按最低信任(low)处理:无法判定信任等级时,宁可保守。
    _tl = getattr(skill, "trust_level", None)
    try:
        trust = _tl() if callable(_tl) else "low"
    except Exception:
        trust = "low"
    downgrade = trust == "low"   # 低信任(mcp/远程)结果须按不可信处理

    # readonly 模式:非只读一律拒绝
    if mode == "readonly" and not read_only:
        return PermissionDecision("deny", 3,
            reason="readonly 模式下禁止非只读操作", trust_downgrade=downgrade)

    # destructive 永不自动放行(即使有 allow 规则也要确认)
    if destructive and not confirmed:
        return PermissionDecision("ask", 3,
            reason="不可逆(destructive)操作,必须用户确认",
            trust_downgrade=downgrade, requires_confirmation=True,
            reason_code=DESTRUCTIVE_CONFIRM)

    # Stage 3.5 污点门槛(Muse 式「碰过敏感数据 → 外发动作追加确认」):
    # 会话已取回 ≥threshold 的用户数据,再外发/副作用 → 即使工具本身可信也强制确认,
    # 防「模型把用户隐私塞进 send/write/external」外泄。置于 trusted 之前:
    # 污点是结构性约束,trusted 只信任工具本身,不豁免「携带敏感数据外发」。
    if taint_threshold and taint_level >= taint_threshold:
        if side_effect in ("write", "send", "pay") or external:
            return PermissionDecision("ask", 3,
                reason="会话已取回用户敏感数据,外发/副作用动作需二次确认(污点防护)",
                trust_downgrade=downgrade, requires_confirmation=True,
                reason_code=TAINT_CONFIRM)

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
        # 【P5-1】三个分支各自的结构化原因码
        code = (ASK_RULE if ask_by_rule else
                EXTERNAL_CONFIRM if external else SIDE_EFFECT_CONFIRM)
        return PermissionDecision("ask", 4,
            reason=f"需确认: {why}", trust_downgrade=downgrade,
            requires_confirmation=True, reason_code=code)

    # 放行:显式 allow 规则,或只读无副作用,或已确认
    reason = ("命中 allow 规则" if allow_by_rule else
              "只读无副作用" if read_only else "已确认")
    return PermissionDecision("allow", 4, reason=reason, trust_downgrade=downgrade)
