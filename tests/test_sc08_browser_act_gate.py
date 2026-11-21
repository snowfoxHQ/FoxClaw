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


def test_write_requires_confirmation():
    act, calls = _recorder()
    r = _run(browser_act(_gate(), act, "click", {"type": "click", "target": "5"}))
    assert r["ok"] is False and r["denied"] is True and not calls
    r2 = _run(browser_act(_gate(), act, "click", {"type": "click", "target": "5"},
                          confirmed=True))
    assert r2["ok"] is True and calls


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
        async def browser_act(self, type_, args, confirmed=False):
            seen["type"] = type_
            seen["args"] = args
            seen["confirmed"] = confirmed
            return {"ok": True}

    monkeypatch.setattr(br._api, "os_instance", lambda: FakeOS())
    p = br.BrowserActPayload(type="scroll", confirmed=True)
    r = asyncio.run(br.browser_act(p))
    assert r == {"ok": True}
    assert seen["type"] == "scroll" and seen["confirmed"] is True
    assert "confirmed" not in seen["args"]  # confirmed 剥离,不透传 act
