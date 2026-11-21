"""FoxClaw — 操作意图授权边界 (Operation Intent Authorization · SC-08)。

browser Web 动作(/api/browser/act)与 Agent 技能路径正交,但共用同一 PolicyGate:
不直接执行 browser_service.act(),先经 check_action 归一化意图 + 授权,deny/confirm
未放行一律 fail-closed 拒绝(与 SC-03 的 observe 对称,不 import permission)。
"""
from __future__ import annotations

from types import SimpleNamespace

# browser action → (side_effect, external)。与 intent.py 的 effect 严格一致;未知 type 宁紧勿松。
_BROWSER_ACTION_META = {
    "navigate": ("write", True),   # 导航发起外部请求且改变页面
    "click": ("write", False),     # 点击可能触发副作用(购买/提交/删除)
    "type": ("write", False),      # 输入可能提交表单/敏感信息
    "scroll": ("read", False),
    "extract": ("read", False),
    "wait": ("read", False),
}


def browser_action_spec(type_: str) -> SimpleNamespace:
    side_effect, external = _BROWSER_ACTION_META.get(type_, ("write", False))
    return SimpleNamespace(id=f"browser:{type_}", kind="browser", side_effect=side_effect,
                           destructive=False, external=external, read_only=None,
                           trust_level=None, params={})


async def browser_act(policy_gate, act, type_, args, rules=None, confirmed=False) -> dict:
    verdict = policy_gate.check_action(browser_action_spec(type_), args or {},
                                       rules=rules, confirmed=confirmed)
    if not verdict.allowed:
        return {"ok": False, "denied": True, "reason": verdict.reason,
                "level": verdict.level, "decision_id": verdict.decision_id}
    return await act(args or {"type": type_})
