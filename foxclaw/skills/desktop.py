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
}

# 域级 intent(domain=desktop, action=category)供 desktop.shell / desktop.file 等域级 deny；
# 精确 read/write 区分用 id 级规则(desktop:clipboard:write)。effect 与 side_effect 严格一致。
for _action, (_se, _ex, _dst) in _DESKTOP_ACTION_META.items():
    _register_intent(f"desktop:{_action}", "desktop", _action.split(":")[0], effect=_se)


def desktop_action_spec(action: str) -> SimpleNamespace:
    side_effect, external, destructive = _DESKTOP_ACTION_META.get(action, ("write", False, True))
    return SimpleNamespace(id=f"desktop:{action}", kind="desktop", side_effect=side_effect,
                           destructive=destructive, external=external, read_only=None,
                           trust_level=None, params={})


async def desktop_act(policy_gate, act, action, args, rules=None, confirmed=False,
                      confirms=None, confirm_nonce: str = "", session: str = "") -> dict:
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
    if not verdict.allowed:
        out = {"ok": False, "denied": True, "reason": verdict.reason,
               "level": verdict.level, "decision_id": verdict.decision_id}
        if confirms is not None and verdict.level == "confirm":
            ch = confirms.issue(session, spec.id, args)
            out["confirm_nonce"] = ch["nonce"]
            out["confirm_expires_at"] = ch["expires_at"]
        if nonce_reject:
            out["nonce_reject"] = nonce_reject
        return out
    result = await act(action, args)
    result = dict(result) if isinstance(result, dict) else {"ok": True, "data": result}
    result["decision_id"] = verdict.decision_id
    return result


# 未显式配置 FOXCLAW_DESKTOP_ACT_DENY 时的默认拒绝:任意 shell 与删文件。
# 显式设置该变量(哪怕设为 none)即完全由使用者接管。
DEFAULT_DESKTOP_DENY = ("desktop:shell:*", "desktop:file:delete")


def desktop_deny_rules(env_value) -> list:
    if env_value is None:
        return list(DEFAULT_DESKTOP_DENY)
    items = [s.strip() for s in str(env_value).split(",") if s.strip()]
    return [] if [s.lower() for s in items] == ["none"] else items
