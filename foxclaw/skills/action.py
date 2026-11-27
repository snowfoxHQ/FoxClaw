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
    # arg_passthrough:browser 动作参数即操作本体,且已按完整参数在本边界完成授权;
    # Serve 契约必须携带同一参数(声明式白名单只用于技能参数)。
    return SimpleNamespace(id=f"browser:{type_}", kind="browser", side_effect=side_effect,
                           destructive=False, external=external, read_only=None,
                           trust_level=None, params={}, arg_passthrough=True)


async def browser_act(policy_gate, act, type_, args, rules=None, confirmed=False,
                      confirms=None, confirm_nonce: str = "", session: str = "",
                      executor=None) -> dict:
    """浏览器 Web 动作授权边界。

    confirms(ConfirmationBroker)注入时,「已确认」不再信任调用方自报的布尔值
    (与 desktop_act 同一模式):写动作首次调用 → check_action 返回 confirm →
    签发一次性 nonce(绑定 动作 + 参数指纹 + 有效期)随拒绝结果返回;再次调用须带
    confirmed=true **且** 该 nonce,consume 校验通过才视为已确认。confirms=None
    保持旧语义,供单测/进程内调用。
    """
    args = args or {}
    spec = browser_action_spec(type_)
    nonce_reject = ""
    if confirms is not None and confirmed:
        ok, why = confirms.consume(session, confirm_nonce, action=spec.id, args=args)
        if not ok:
            confirmed = False
            nonce_reject = why
    verdict = policy_gate.check_action(spec, args, rules=rules, confirmed=confirmed)
    if verdict.level == "deny":
        return {"ok": False, "denied": True, "reason": verdict.reason,
                "level": verdict.level, "decision_id": verdict.decision_id,
                "execution_backend": "blocked"}
    route = (executor.route(spec) if executor is not None
             and spec.side_effect != "read" else None)
    if route is not None and route.backend == "blocked":
        return {"ok": False, "denied": True, "reason": route.message,
                "level": "blocked", "decision_id": verdict.decision_id,
                "execution_backend": "blocked"}
    if route is not None and route.backend == "serve":
        # 与技能路径同一语义:确认由 Serve 契约审批承担,不走本地 nonce。
        return executor.submit_action(spec, args, decision_id=verdict.decision_id)
    if verdict.level == "confirm":
        out = {"ok": False, "denied": True, "reason": verdict.reason,
               "level": verdict.level, "decision_id": verdict.decision_id,
               "execution_backend": "blocked"}
        if confirms is not None:
            ch = confirms.issue(session, spec.id, args)
            out["confirm_nonce"] = ch["nonce"]
            out["confirm_expires_at"] = ch["expires_at"]
        if nonce_reject:
            out["nonce_reject"] = nonce_reject
        return out
    result = await act(args or {"type": type_})
    if isinstance(result, dict):
        result = dict(result)
        result["execution_backend"] = "local"
        if route is not None and route.degraded:
            result["execution_warning"] = route.message
    return result
