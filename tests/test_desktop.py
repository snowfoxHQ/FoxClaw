"""桌面操控子系统(感知层 + 操作层)—— 安全闭环合成测试 + shell/文件真实后端测试。"""
import pytest

from foxclaw.core.injection_guard import TAINT_SENSITIVE, taint_level
from foxclaw.desktop import DesktopService
from foxclaw.kernel.policy_gate import PolicyGate
from foxclaw.skills.observation import ObservationRules, evaluate_observation, observe
from foxclaw.skills.permission import PermissionRules, evaluate as _perm_eval
from foxclaw.skills.desktop import desktop_act, desktop_action_spec
from foxclaw.skills.intent import canonicalize


def _gate():
    return PolicyGate(observation_evaluator=evaluate_observation)


def test_screen_windows_in_known_sources():
    from foxclaw.skills.observation import _KNOWN_SOURCES
    assert "screen" in _KNOWN_SOURCES and "windows" in _KNOWN_SOURCES
    assert taint_level("screen") == TAINT_SENSITIVE
    assert taint_level("windows") == TAINT_SENSITIVE


def test_windows_taint_sensitive_and_others_unchanged():
    from foxclaw.core.injection_guard import TAINT_EXTERNAL, TAINT_PERSONAL
    assert taint_level("windows") == TAINT_SENSITIVE
    assert taint_level("screen") == TAINT_SENSITIVE
    assert taint_level("uia") == TAINT_SENSITIVE
    assert taint_level("ocr") == TAINT_SENSITIVE
    assert taint_level("browser") == TAINT_PERSONAL
    assert taint_level("file") == TAINT_PERSONAL
    assert taint_level("clipboard") == TAINT_PERSONAL
    assert taint_level("knowledge") == TAINT_PERSONAL
    assert taint_level("memory") == TAINT_SENSITIVE
    assert taint_level("no-such-source") == TAINT_EXTERNAL
    assert TAINT_SENSITIVE > TAINT_PERSONAL


def test_desktop_service_status(monkeypatch):
    monkeypatch.setattr(DesktopService, "available", staticmethod(lambda: False))
    assert DesktopService().status()["available"] is False


@pytest.mark.anyio
async def test_capture_unavailable_degrades(monkeypatch):
    monkeypatch.setattr(DesktopService, "available", staticmethod(lambda: False))
    r = await DesktopService().capture()
    assert r["ok"] is False and r["available"] is False


@pytest.mark.anyio
async def test_observe_screen_allowed_wraps_and_taints():
    async def fake_perceive():
        return {"ok": True, "image": {"width": 1, "height": 1, "data": "abc"}}

    r = await observe(_gate(), fake_perceive, "screen", "full",
                      rules=ObservationRules(allow=["screen:*"]))
    assert r.allowed and 'source="screen"' in r.wrapped
    assert r.taint == TAINT_SENSITIVE


@pytest.mark.anyio
async def test_observe_screen_denied_fail_closed():
    calls = []

    async def fake_perceive():
        calls.append(1)
        return {"ok": True}

    r = await observe(_gate(), fake_perceive, "screen", "full",
                      rules=ObservationRules())
    assert r.allowed is False and calls == []
    assert r.raw is None and r.wrapped == ""


@pytest.mark.anyio
async def test_observe_windows_allowed_wraps():
    async def fake_perceive():
        return {"ok": True, "windows": [{"handle": 1, "title": "X", "class": "Y"}]}

    r = await observe(_gate(), fake_perceive, "windows", "full",
                      rules=ObservationRules(allow=["windows:*"]))
    assert r.allowed and 'source="windows"' in r.wrapped
    assert r.taint == TAINT_SENSITIVE


# ── 操作层 ──

def _action_gate():
    return PolicyGate(action_evaluator=_perm_eval)


def test_desktop_action_spec_meta():
    s = desktop_action_spec("shell:run")
    assert s.id == "desktop:shell:run"
    assert s.side_effect == "write" and s.external and s.destructive
    f = desktop_action_spec("file:read")
    assert f.side_effect == "read" and not f.destructive
    assert desktop_action_spec("file:delete").destructive
    assert desktop_action_spec("window:close").destructive
    # 未知动作宁紧勿松(destructive=True)
    assert desktop_action_spec("unknown:x").destructive


def test_desktop_intent_registered():
    i = canonicalize("desktop:shell:run")
    assert i is not None and i.domain == "desktop" and i.action == "shell"
    assert canonicalize("desktop:file:read").effect == "read"


@pytest.mark.anyio
async def test_desktop_act_write_requires_confirm():
    calls = []

    async def fake_act(action, args):
        calls.append(action)
        return {"ok": True}

    r = await desktop_act(_action_gate(), fake_act, "shell:run",
                          {"command": "echo hi"}, confirmed=False)
    assert r["denied"] is True and r["level"] == "confirm"
    assert calls == []


@pytest.mark.anyio
async def test_desktop_act_deny_rule_fail_closed():
    calls = []

    async def fake_act(action, args):
        calls.append(action)
        return {"ok": True}

    r = await desktop_act(_action_gate(), fake_act, "shell:run",
                          {"command": "rm -rf /"},
                          rules=PermissionRules(deny=["desktop:shell:*"]),
                          confirmed=True)
    assert r["denied"] is True and calls == []


@pytest.mark.anyio
async def test_desktop_act_confirmed_allows():
    async def fake_act(action, args):
        return {"ok": True, "action": action}

    r = await desktop_act(_action_gate(), fake_act, "file:write",
                          {"path": "x.txt", "content": "hi"}, confirmed=True)
    assert r["ok"] is True and r.get("decision_id")


@pytest.mark.anyio
async def test_desktop_act_read_no_confirm():
    async def fake_act(action, args):
        return {"ok": True, "action": action}

    r = await desktop_act(_action_gate(), fake_act, "file:read",
                          {"path": "x.txt"}, confirmed=False)
    assert r["ok"] is True


# ── 写动作确认:一次性 nonce(不再信任调用方自报 confirmed=true)──

def _broker():
    from foxclaw.core.confirm_nonce import ConfirmationBroker
    return ConfirmationBroker()


def _recorder():
    calls = []

    async def fake_act(action, args):
        calls.append((action, dict(args)))
        return {"ok": True, "action": action}
    return calls, fake_act


@pytest.mark.anyio
async def test_confirmed_true_without_nonce_is_rejected():
    calls, fake_act = _recorder()
    r = await desktop_act(_action_gate(), fake_act, "file:write",
                          {"path": "x.txt", "content": "hi"}, confirmed=True,
                          confirms=_broker())
    assert r["denied"] is True and r["level"] == "confirm"
    assert r["nonce_reject"] == "missing_nonce"
    assert r.get("confirm_nonce") and calls == []


@pytest.mark.anyio
async def test_nonce_roundtrip_allows_once():
    b = _broker()
    calls, fake_act = _recorder()
    args = {"path": "x.txt", "content": "hi"}
    first = await desktop_act(_action_gate(), fake_act, "file:write", args, confirms=b)
    nonce = first["confirm_nonce"]
    ok = await desktop_act(_action_gate(), fake_act, "file:write", args,
                           confirmed=True, confirms=b, confirm_nonce=nonce)
    assert ok["ok"] is True and calls == [("file:write", args)]
    replay = await desktop_act(_action_gate(), fake_act, "file:write", args,
                               confirmed=True, confirms=b, confirm_nonce=nonce)
    assert replay["denied"] is True and replay["nonce_reject"] in ("unknown_nonce", "already_used")
    assert len(calls) == 1


@pytest.mark.anyio
async def test_nonce_bound_to_args_and_action():
    b = _broker()
    calls, fake_act = _recorder()
    first = await desktop_act(_action_gate(), fake_act, "file:write",
                              {"path": "a.txt", "content": "hi"}, confirms=b)
    nonce = first["confirm_nonce"]
    # 确认的是写 a.txt,拿去写 b.txt → 参数指纹不符
    r = await desktop_act(_action_gate(), fake_act, "file:write",
                          {"path": "b.txt", "content": "hi"},
                          confirmed=True, confirms=b, confirm_nonce=nonce)
    assert r["denied"] is True and r["nonce_reject"] == "args_mismatch" and calls == []
    # 换动作也不行
    first = await desktop_act(_action_gate(), fake_act, "keyboard:type", {"text": "a"}, confirms=b)
    r = await desktop_act(_action_gate(), fake_act, "mouse:click", {"text": "a"},
                          confirmed=True, confirms=b, confirm_nonce=first["confirm_nonce"])
    assert r["denied"] is True and r["nonce_reject"] == "action_mismatch" and calls == []


@pytest.mark.anyio
async def test_expired_nonce_rejected():
    from foxclaw.core.confirm_nonce import ConfirmationBroker
    now = [1000.0]
    b = ConfirmationBroker(ttl=60, clock=lambda: now[0])
    calls, fake_act = _recorder()
    args = {"path": "x.txt", "content": "hi"}
    first = await desktop_act(_action_gate(), fake_act, "file:write", args, confirms=b)
    now[0] += 61
    r = await desktop_act(_action_gate(), fake_act, "file:write", args, confirmed=True,
                          confirms=b, confirm_nonce=first["confirm_nonce"])
    assert r["denied"] is True and r["nonce_reject"] == "expired" and calls == []


@pytest.mark.anyio
async def test_read_action_needs_no_nonce():
    calls, fake_act = _recorder()
    r = await desktop_act(_action_gate(), fake_act, "file:read", {"path": "x.txt"},
                          confirms=_broker())
    assert r["ok"] is True and "confirm_nonce" not in r


def test_default_deny_rules():
    from foxclaw.skills.desktop import desktop_deny_rules, DEFAULT_DESKTOP_DENY
    assert desktop_deny_rules(None) == list(DEFAULT_DESKTOP_DENY)
    assert desktop_deny_rules("none") == []
    assert desktop_deny_rules("") == []
    assert desktop_deny_rules("desktop:keyboard:*, desktop:shell:*") == [
        "desktop:keyboard:*", "desktop:shell:*"]


@pytest.mark.anyio
async def test_default_deny_blocks_shell_and_delete_even_with_valid_nonce():
    from foxclaw.skills.desktop import desktop_deny_rules
    rules = PermissionRules(deny=desktop_deny_rules(None))
    b = _broker()
    calls, fake_act = _recorder()
    for action, args in (("shell:run", {"command": "echo hi"}), ("file:delete", {"path": "x"})):
        r = await desktop_act(_action_gate(), fake_act, action, args, rules=rules, confirms=b)
        assert r["denied"] is True and r["level"] == "deny"
        assert "confirm_nonce" not in r   # deny 不签发确认码,确认也救不回来
    assert calls == []


# ── 真实后端(shell/file 跨平台标准库)──

@pytest.mark.anyio
async def test_shell_run_real():
    r = await DesktopService().act("shell:run", {"command": "echo foxclaw-desktop-test"})
    assert r["ok"] and "foxclaw-desktop-test" in r["stdout"]


@pytest.mark.anyio
async def test_file_write_read_delete_real(tmp_path):
    svc = DesktopService()
    p = str(tmp_path / "a.txt")
    assert (await svc.act("file:write", {"path": p, "content": "hello"}))["ok"]
    assert (await svc.act("file:read", {"path": p}))["content"] == "hello"
    assert (await svc.act("file:write", {"path": p, "content": "!", "append": True}))["ok"]
    assert (await svc.act("file:read", {"path": p}))["content"] == "hello!"
    assert (await svc.act("file:delete", {"path": p}))["ok"]
    assert (await svc.act("file:read", {"path": p}))["ok"] is False


@pytest.mark.anyio
async def test_platform_gated_ops():
    """键鼠/窗口/剪贴板：Windows 走真实 ctypes，非 Windows 结构化降级。"""
    svc = DesktopService()
    r = await svc.act("clipboard:read", {})
    if svc.windows_supported():
        assert "ok" in r  # Windows 至少返回结构化结果
    else:
        assert r["ok"] is False and r["available"] is False


# ── UI Automation ──

def test_uia_in_known_sources_and_taint():
    from foxclaw.skills.observation import _KNOWN_SOURCES
    assert "uia" in _KNOWN_SOURCES
    assert taint_level("uia") == TAINT_SENSITIVE


def test_ui_action_spec_meta():
    s = desktop_action_spec("ui:invoke")
    assert s.id == "desktop:ui:invoke"
    assert s.side_effect == "write" and not s.external and not s.destructive
    assert desktop_action_spec("ui:set_value").side_effect == "write"
    assert desktop_action_spec("ui:click").side_effect == "write"


def test_ui_intent_registered():
    i = canonicalize("desktop:ui:invoke")
    assert i is not None and i.domain == "desktop" and i.action == "ui"
    assert i.effect == "write"


@pytest.mark.anyio
async def test_ui_act_requires_confirm():
    calls = []

    async def fake_act(action, args):
        calls.append(action)
        return {"ok": True}

    r = await desktop_act(_action_gate(), fake_act, "ui:invoke",
                          {"locator": {"index": 0}}, confirmed=False)
    assert r["denied"] is True and r["level"] == "confirm"
    assert calls == []


@pytest.mark.anyio
async def test_ui_act_confirmed_allows():
    async def fake_act(action, args):
        return {"ok": True, "action": action}

    r = await desktop_act(_action_gate(), fake_act, "ui:invoke",
                          {"locator": {"index": 0}}, confirmed=True)
    assert r["ok"] is True


@pytest.mark.anyio
async def test_ui_click_locates_then_clicks(monkeypatch):
    from foxclaw.desktop import service as svc_mod
    seen = {}

    def fake_ui(action, locator, value=""):
        seen["locate"] = (action, locator)
        return {"ok": True, "center": [300, 200]}

    def fake_mouse(action, x, y):
        seen["click"] = (action, x, y)
        return {"ok": True}

    monkeypatch.setattr(svc_mod, "ui_action_sync", fake_ui)
    monkeypatch.setattr(svc_mod, "mouse_sync", fake_mouse)
    r = await DesktopService().act("ui:click", {"locator": {"name": "确定"}})
    assert r["ok"] is True
    assert seen["locate"] == ("locate", {"name": "确定"})
    assert seen["click"] == ("click", 300, 200)


def test_ui_action_sync_locator_normalize(monkeypatch):
    from foxclaw.desktop import uia
    captured = {}

    def fake_run(script, env_extra, timeout=15):
        captured.update(env_extra)
        return {"ok": True}

    monkeypatch.setattr(uia.sys, "platform", "win32")
    monkeypatch.setattr(uia, "_run_powershell", fake_run)
    uia.ui_action_sync("invoke", 5, "hi")
    assert captured["FOXCLAW_UIA_ACTION"] == "invoke"
    assert captured["FOXCLAW_UIA_LOCATOR"] == '{"index": 5}'
    assert captured["FOXCLAW_UIA_VALUE"] == "hi"


@pytest.mark.anyio
async def test_ui_tree_unavailable_degrades(monkeypatch):
    from foxclaw.desktop import service as svc_mod
    monkeypatch.setattr(svc_mod, "uia_available", lambda: False)
    r = await DesktopService().ui_tree()
    assert r["ok"] is False and r["available"] is False


@pytest.mark.anyio
async def test_observe_uia_allowed_wraps_and_taints():
    async def fake_perceive():
        return {"ok": True, "count": 1, "elements": [{"index": 0, "name": "确定"}]}

    r = await observe(_gate(), fake_perceive, "uia", "full",
                      rules=ObservationRules(allow=["uia:*"]))
    assert r.allowed and 'source="uia"' in r.wrapped
    assert r.taint == TAINT_SENSITIVE


# ── 屏幕 OCR ──

def test_ocr_in_known_sources_and_taint():
    from foxclaw.skills.observation import _KNOWN_SOURCES
    assert "ocr" in _KNOWN_SOURCES
    assert taint_level("ocr") == TAINT_SENSITIVE


@pytest.mark.anyio
async def test_ocr_unavailable_degrades(monkeypatch):
    from foxclaw.desktop import service as svc_mod
    monkeypatch.setattr(DesktopService, "available", staticmethod(lambda: True))
    monkeypatch.setattr(svc_mod, "ocr_available", lambda: False)
    r = await DesktopService().ocr()
    assert r["ok"] is False and r["available"] is False


@pytest.mark.anyio
async def test_observe_ocr_allowed_wraps_and_taints():
    async def fake_perceive():
        return {"ok": True, "text": "确定", "lines": []}

    r = await observe(_gate(), fake_perceive, "ocr", "full",
                      rules=ObservationRules(allow=["ocr:*"]))
    assert r.allowed and 'source="ocr"' in r.wrapped
    assert r.taint == TAINT_SENSITIVE


def test_ocr_sync_env_roundtrip(monkeypatch):
    import base64
    from foxclaw.desktop import ocr
    captured = {}

    def fake_capture():
        return {"ok": True, "image": {"data": base64.b64encode(b"fake-png").decode("ascii")}}

    def fake_run(script, env_extra, timeout=30):
        captured.update(env_extra)
        return {"ok": True, "text": "x"}

    monkeypatch.setattr(ocr.sys, "platform", "win32")
    monkeypatch.setattr(ocr, "capture_screen", fake_capture)
    monkeypatch.setattr(ocr, "_run_powershell", fake_run)
    r = ocr.ocr_sync("zh-CN")
    assert r["ok"] is True
    assert captured["FOXCLAW_OCR_LANG"] == "zh-CN"
    assert captured["FOXCLAW_OCR_IMAGE"].endswith(".png")
