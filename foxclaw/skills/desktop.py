"""FoxClaw — 桌面操作意图授权边界 (Desktop Operation Authorization · SC-08 扩展)。

桌面操控动作(键鼠/窗口/剪贴板/shell/文件)与 browser 一样，不直接执行
desktop_service.act()，先经 check_action 归一化意图 + 授权，deny/confirm 未放行
一律 fail-closed 拒绝。意图映射(desktop.* 域级)在模块 import 时注入共享 canonicalizer。
"""
from __future__ import annotations

from types import SimpleNamespace

from .intent import register as _register_intent

# desktop action(category:sub) → (side_effect, external, destructive)。未知 action 宁紧勿松。
_DESKTOP_ACTION_META = {
    "mouse:move": ("write", False, False),
    "mouse:click": ("write", False, False),
    "mouse:right_click": ("write", False, False),
    "mouse:double_click": ("write", False, False),
    "mouse:drag": ("write", False, False),
    "mouse:scroll": ("write", False, False),
    "keyboard:type": ("write", False, False),
    "keyboard:press": ("write", False, False),
    "keyboard:hotkey": ("write", False, False),
    "window:focus": ("write", False, False),
    "window:close": ("write", False, True),
    "window:minimize": ("write", False, False),
    "window:maximize": ("write", False, False),
    "window:restore": ("write", False, False),
    "window:move": ("write", False, False),
    "window:resize": ("write", False, False),
    "clipboard:read": ("read", False, False),
    "clipboard:write": ("write", False, False),
    "shell:run": ("write", True, True),
    "file:read": ("read", False, False),
    "file:write": ("write", False, False),
    "file:delete": ("write", False, True),
    # UI Automation：对元素的精确操控（比坐标点击更可靠）。均 write（触发/改值有副作用）。
    "ui:invoke": ("write", False, False),
    "ui:set_value": ("write", False, False),
    "ui:select": ("write", False, False),
    "ui:toggle": ("write", False, False),
    "ui:expand": ("write", False, False),
    "ui:collapse": ("write", False, False),
    "ui:click": ("write", False, False),
    # 【G4】批量 UIA 操作:整批参数作为一次动作授权/确认(批内不产生豁免)。
    "ui:batch": ("write", False, False),
}

# 域级 intent(domain=desktop, action=category)供 desktop.shell / desktop.file 等域级 deny；
# 精确 read/write 区分用 id 级规则(desktop:clipboard:write)。effect 与 side_effect 严格一致。
for _action, (_se, _ex, _dst) in _DESKTOP_ACTION_META.items():
    _register_intent(f"desktop:{_action}", "desktop", _action.split(":")[0], effect=_se)


def desktop_action_spec(action: str) -> SimpleNamespace:
    side_effect, external, destructive = _DESKTOP_ACTION_META.get(action, ("write", False, True))
    # arg_passthrough:桌面动作参数即操作本体,且已按完整参数在本边界完成授权;
    # Serve 契约必须携带同一参数(声明式白名单只用于技能参数)。
    return SimpleNamespace(id=f"desktop:{action}", kind="desktop", side_effect=side_effect,
                           destructive=destructive, external=external, read_only=None,
                           trust_level=None, params={}, arg_passthrough=True)


def _channel_guard(plan, probes, action: str) -> dict | None:
    """【G4·三通道】执行前确定性校验:声明了非 Visual 首选时,Visual 动作必须有降级证据。

    这是 Runtime 决策点,不是授权:deny/confirm 仍由 PolicyGate 先行、独立裁决;
    无声明(默认)时本守卫不干预,行为与改前一致。见 core/channel_policy。
    """
    from ..core import channel_policy as _chan
    why = _chan.visual_gate(plan, probes, action)
    if not why:
        return None
    return {"ok": False, "denied": True, "reason": why, "level": "blocked",
            "channel_guard": True, "execution_backend": "blocked"}


def _active_task_ctx():
    """读当前任务的 TaskContext(contextvar;HTTP 直连路径无任务上下文 → None,守卫不干预)。

    懒导入防循环:agents.mixins._context → core.*;skills.desktop 在调用期才取。
    """
    try:
        from ..agents.mixins._context import _active_tc
        return _active_tc.get()
    except Exception:
        return None


def _tc_plan() -> dict:
    tc = _active_task_ctx()
    try:
        return tc.channel_plan if tc is not None else {}
    except Exception:
        return {}


def _tc_probes() -> list:
    tc = _active_task_ctx()
    try:
        return (tc.to_dict().get("channel_probes") or []) if tc is not None else []
    except Exception:
        return []


async def desktop_act(policy_gate, act, action, args, rules=None, confirmed=False,
                      confirms=None, confirm_nonce: str = "", session: str = "",
                      executor=None) -> dict:
    """桌面动作授权边界。

    confirms(ConfirmationBroker)注入时,「已确认」不再信任调用方自报的布尔值:
      · 写动作首次调用 → check_action 返回 confirm → 签发一次性 nonce
        (绑定 动作 + 参数指纹 + 有效期,默认 300s)随拒绝结果返回;
      · 再次调用须带 confirmed=true **且** 该 nonce,consume 校验通过才视为已确认;
        nonce 缺失 / 过期 / 已用 / 参数被改 → 一律按未确认处理(fail-closed)。
    堵住的是「一个请求 confirmed=true 直接放行」「确认 A 执行 B」「同一确认反复重放」。
    边界(与 confirm_nonce 模块一致,诚实):nonce 只证明调用方看到过这次待确认,
    不证明是人按的;真正的人在回路要由 UI/渠道把人接进环路。高危动作另靠默认 deny 兜底。

    confirms=None 时保持旧语义(confirmed 布尔直接生效),供单测/进程内调用。
    """
    args = args or {}
    spec = desktop_action_spec(action)
    nonce_reject = ""
    if confirms is not None and confirmed:
        ok, why = confirms.consume(session, confirm_nonce, action=spec.id, args=args)
        if not ok:
            confirmed = False
            nonce_reject = why
    verdict = policy_gate.check_action(spec, args, rules=rules, confirmed=confirmed)
    if verdict.level == "deny":
        out = {"ok": False, "denied": True, "reason": verdict.reason,
               "level": verdict.level, "decision_id": verdict.decision_id,
               "execution_backend": "blocked"}
        if confirms is not None and verdict.level == "confirm":
            ch = confirms.issue(session, spec.id, args)
            out["confirm_nonce"] = ch["nonce"]
            out["confirm_expires_at"] = ch["expires_at"]
        if nonce_reject:
            out["nonce_reject"] = nonce_reject
        return out
    # 【G4·三通道】授权之后、执行之前的 Runtime 侧确定性校验:声明了非 Visual
    # 首选时,Visual 动作必须有探测降级证据(声明制——无声明不干预)。
    _chan_guard = _channel_guard(_tc_plan(), _tc_probes(), action)
    if _chan_guard is not None:
        _chan_guard["decision_id"] = verdict.decision_id
        return _chan_guard
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
    result = await act(action, args)
    result = dict(result) if isinstance(result, dict) else {"ok": True, "data": result}
    result["decision_id"] = verdict.decision_id
    result["execution_backend"] = "local"
    if route is not None and route.degraded:
        result["execution_warning"] = route.message
    return result


# 未显式配置 FOXCLAW_DESKTOP_ACT_DENY 时的默认拒绝:任意 shell 与删文件。
# 显式设置该变量(哪怕设为 none)即完全由使用者接管。
DEFAULT_DESKTOP_DENY = ("desktop:shell:*", "desktop:file:delete")


def desktop_deny_rules(env_value) -> list:
    if env_value is None:
        return list(DEFAULT_DESKTOP_DENY)
    items = [s.strip() for s in str(env_value).split(",") if s.strip()]
    return [] if [s.lower() for s in items] == ["none"] else items
