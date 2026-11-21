"""FoxClaw — 观察权限通道 (Observation Permission · SC-02)。

感知(读屏/读页/读剪贴板)与操作(写/发/付)是正交风险:感知暴露隐私,操作产生
副作用。观察通道独立授权,不复用操作通道的 read;未授权一律 fail-closed。

本模块不 import permission(与操作通道并列);PolicyGate.check_observation 按 channel
分流到这里。
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field

from ..core.injection_guard import taint_level, wrap_untrusted

# 已知感知 source 白名单(约束7:未声明 source → fail-closed)。
_KNOWN_SOURCES = {"screen", "browser", "clipboard", "uia", "windows"}

# sensitivity → taint_level(SC-03 污点传播消费,SC-02 只定义语义)。
_SENSITIVITY_TAINT = {"public": 0, "private": 1, "sensitive": 2}


@dataclass(frozen=True)
class ObservationDecision:
    decision: str        # "allow" | "deny"
    reason: str = ""
    stage: int = 0
    taint: int = 0       # sensitivity → taint_level,SC-03 污点传播消费


@dataclass
class ObservationRules:
    """观察授权规则:allow / deny 均为 `source:scope` fnmatch 模式。"""
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)


def _hits(patterns: list[str], key: str) -> bool:
    return any(fnmatch.fnmatch(key, p) for p in patterns)


def evaluate_observation(obs, rules: ObservationRules | None = None) -> ObservationDecision:
    """观察通道判定(鸭子类型:取 obs.source / obs.scope / obs.sensitivity)。

    顺序:未声明 source → fail-closed 拒;deny 命中 → 拒;allow 命中(scope 覆盖) → 放;
    其余 → 拒(未授权不观察)。
    """
    rules = rules or ObservationRules()
    source = getattr(obs, "source", "")
    scope = getattr(obs, "scope", "")
    key = f"{source}:{scope}"

    if source not in _KNOWN_SOURCES:
        return ObservationDecision("deny", f"未声明感知源,fail-closed: {source}", stage=1)
    if _hits(rules.deny, key) or _hits(rules.deny, f"{source}:*"):
        return ObservationDecision("deny", f"命中观察 deny 规则: {key}", stage=1)
    if _hits(rules.allow, key) or _hits(rules.allow, f"{source}:*"):
        return ObservationDecision("allow", f"命中观察 allow 规则: {key}", stage=2,
                                   taint=_SENSITIVITY_TAINT.get(
                                       getattr(obs, "sensitivity", "public"), 0))
    return ObservationDecision("deny", f"未授权观察: {key}", stage=1)


@dataclass
class ObservationResult:
    """感知准入的最终产物。allowed=False 时无 wrapped/raw/taint(未感知)。

    wrapped 是唯一可注入决策上下文的文本——已过 wrap_untrusted(source),任何消费方
    拿到它都天然带 source + nonce + 伪造授权告警;未包装的感知数据在此结构外无出口。
    """
    allowed: bool
    source: str
    scope: str
    wrapped: str = ""
    raw: object = None
    taint: int = 0
    reason: str = ""
    decision_id: str = ""


def _serialize(raw) -> str:
    """感知结果 → 可注入文本。str 原样;dict/list 走 json(结构化语义);其余 str()。"""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (dict, list)):
        return json.dumps(raw, ensure_ascii=False, default=str)
    return str(raw)


async def observe(policy_gate, perceive, source, scope, sensitivity="public",
                  rules=None) -> ObservationResult:
    """统一感知准入边界(SC-03)。Agent 与 API 共用;perceive 依赖注入(不 import browser)。

    硬约束:先 check_observation 后 perceive——deny 时绝不到达 perceive();放行后的结果
    必过 wrap_untrusted(source) 才返回,未包装感知数据无出口。污点按来源分级
    (taint_level(source)):browser=个人数据级、screen=最高敏感级。
    """
    from .intent import ObservationIntent
    verdict = policy_gate.check_observation(
        ObservationIntent(source, scope, sensitivity), rules=rules)
    if not verdict.allowed:
        return ObservationResult(False, source, scope, reason=verdict.reason,
                                 decision_id=verdict.decision_id)
    raw = await perceive()
    wrapped = wrap_untrusted(_serialize(raw), source=source)
    return ObservationResult(True, source, scope, wrapped=wrapped, raw=raw,
                             taint=taint_level(source), decision_id=verdict.decision_id)
