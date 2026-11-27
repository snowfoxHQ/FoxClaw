"""SC-08·browser/act 操作意图授权核验。

核验:/api/browser/act 不再直接执行,先经 OperationIntent + PolicyGate.check_action——
  deny → 拒绝;write 副作用未确认 → 拒绝(fail-closed);read → 放行;无评估器 → 拒绝。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.core.confirm_nonce import ConfirmationBroker
from foxclaw.kernel.policy_gate import PolicyGate
from foxclaw.skills.action import browser_act, browser_action_spec
from foxclaw.skills.intent import IntentCanonicalizer, OperationIntent
from foxclaw.skills.permission import PermissionRules, evaluate as _perm_eval


def _gate():
    return PolicyGate(action_evaluator=_perm_eval)


def _run(coro):
    return asyncio.run(coro)


def _recorder():
    calls = []

    async def act(args):
        calls.append(args)
        return {"ok": True}

    return act, calls


def test_spec_side_effect_consistent():
    assert browser_action_spec("navigate").side_effect == "write"
    assert browser_action_spec("navigate").external is True
    assert browser_action_spec("click").side_effect == "write"
    assert browser_action_spec("type").side_effect == "write"
    assert browser_action_spec("scroll").side_effect == "read"
    assert browser_action_spec("extract").side_effect == "read"
    assert browser_action_spec("wait").side_effect == "read"
    assert browser_action_spec("unknown").side_effect == "write"  # 未知宁紧勿松


def test_intent_normalized():
    c = IntentCanonicalizer()
    assert c.canonicalize("browser:navigate", {"target": "https://x"}) == \
        OperationIntent(domain="browser", action="navigate", target="https://x", effect="write")
    assert c.canonicalize("browser:click", {}).to_key() == "browser.click"
    assert c.canonicalize("browser:scroll", {}).effect == "read"
    assert c.canonicalize("browser:navigate", {}) is None  # 缺 target → 不猜


def test_deny_blocks_by_id():
    act, calls = _recorder()
    rules = PermissionRules(deny=["browser:navigate"])
    r = _run(browser_act(_gate(), act, "navigate", {"type": "navigate", "target": "https://x"},
                         rules=rules))
    assert r["ok"] is False and r["denied"] is True
    assert not calls


def test_deny_blocks_by_intent_key():
    act, calls = _recorder()
    rules = PermissionRules(deny=["browser.navigate"])  # 意图键 domain.action 也拦
    r = _run(browser_act(_gate(), act, "navigate", {"type": "navigate", "target": "https://x"},
                         rules=rules))
    assert r["ok"] is False and r["denied"] is True
    assert not calls


def test_write_requires_nonce_confirmation():
    act, calls = _recorder()
    broker = ConfirmationBroker(clock=lambda: 1000.0)
    # 首次调用 → confirm 拒绝 + 签发一次性 nonce
    r = _run(browser_act(_gate(), act, "click", {"type": "click", "target": "5"},
                         confirms=broker))
    assert r["ok"] is False and r["denied"] is True and not calls
    assert r["confirm_nonce"] and r["confirm_expires_at"]
    # 只带 confirmed=true 不带 nonce → 仍按未确认处理,换发新码
    r2 = _run(browser_act(_gate(), act, "click", {"type": "click", "target": "5"},
                          confirmed=True, confirms=broker))
    assert r2["ok"] is False and not calls and r2["confirm_nonce"]
    # 带正确 nonce → 放行
    r3 = _run(browser_act(_gate(), act, "click", {"type": "click", "target": "5"},
                          confirmed=True, confirm_nonce=r["confirm_nonce"],
                          confirms=broker))
    assert r3["ok"] is True and calls
    # 同一 nonce 重放 → 已作废,退回 confirm
    r4 = _run(browser_act(_gate(), act, "click", {"type": "click", "target": "5"},
                          confirmed=True, confirm_nonce=r["confirm_nonce"],
                          confirms=broker))
    assert r4["ok"] is False and r4.get("nonce_reject") == "unknown_nonce"


def test_nonce_binds_args():
    act, calls = _recorder()
    broker = ConfirmationBroker(clock=lambda: 1000.0)
    r = _run(browser_act(_gate(), act, "click", {"type": "click", "target": "5"},
                         confirms=broker))
    # 确认 A 点 B(target 漂移)→ 参数指纹不匹配,拒绝
    r2 = _run(browser_act(_gate(), act, "click", {"type": "click", "target": "6"},
                          confirmed=True, confirm_nonce=r["confirm_nonce"],
                          confirms=broker))
    assert r2["ok"] is False and r2.get("nonce_reject") == "args_mismatch" and not calls


def test_confirms_none_keeps_legacy_boolean():
    """未注入 broker 的路径(单测/进程内调用)保持旧布尔语义。"""
    act, calls = _recorder()
    r = _run(browser_act(_gate(), act, "click", {"type": "click", "target": "5"},
                         confirmed=True))
    assert r["ok"] is True and calls


def test_read_allowed_without_confirmation():
    act, calls = _recorder()
    r = _run(browser_act(_gate(), act, "scroll", {"type": "scroll"}))
    assert r["ok"] is True and calls


def test_fail_closed_no_evaluator():
    act, calls = _recorder()
    r = _run(browser_act(PolicyGate(), act, "scroll", {"type": "scroll"}))
    assert r["ok"] is False and r["denied"] is True and not calls


def test_route_uses_browser_act(monkeypatch):
    import foxclaw.api.routes.browser as br

    seen = {}

    class FakeOS:
        async def browser_act(self, type_, args, confirmed=False, confirm_nonce=""):
            seen["type"] = type_
            seen["args"] = args
            seen["confirmed"] = confirmed
            seen["confirm_nonce"] = confirm_nonce
            return {"ok": True}

    monkeypatch.setattr(br._api, "os_instance", lambda: FakeOS())
    p = br.BrowserActPayload(type="scroll", confirmed=True, confirm_nonce="n-1")
    r = asyncio.run(br.browser_act(p))
    assert r == {"ok": True}
    assert seen["type"] == "scroll" and seen["confirmed"] is True
    assert seen["confirm_nonce"] == "n-1"
    assert "confirmed" not in seen["args"]  # confirmed/nonce 剥离,不透传 act
    assert "confirm_nonce" not in seen["args"]
