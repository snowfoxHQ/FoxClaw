"""SC-04·工具路径接入 PolicyGate(_guard_tool 经统一门,消除 F5 旁路)。

覆盖:有 gate 走 check_action(deny/ask/allow 三分支)、fail-closed(raw=None)、
无 gate 时 fallback 直接 permission.evaluate(standalone 行为不变)。
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.agents.mixins.tool_exec import _guard_tool
from foxclaw.kernel import PolicyGate
from foxclaw.kernel.policy_gate import Verdict
from foxclaw.skills.permission import PermissionDecision


class _FakeGate:
    """记录 check_action 调用并返回指定裁决的工具路径替身。"""

    def __init__(self, decision: str):
        self.calls: list = []
        self.decision = decision

    def check_action(self, skill, args, **kw):
        self.calls.append((skill.id, args, kw))
        d = PermissionDecision(self.decision, 0, reason=f"fake:{self.decision}")
        return Verdict(
            allowed=(self.decision == "allow"),
            level={"allow": "allow", "ask": "confirm", "deny": "deny"}[self.decision],
            reason=d.reason, source="permission", raw=d)


def _agent(caps):
    return types.SimpleNamespace(_think=lambda **k: None, _caps=caps)


def test_gate_deny():
    gate = _FakeGate("deny")
    out, cap = _guard_tool(_agent({"policy_gate": gate}), "recall_memory", {"query": "x"})
    assert gate.calls, "有 gate 时必须走 check_action,不得绕过"
    assert "拒绝" in out


def test_gate_ask():
    gate = _FakeGate("ask")
    out, cap = _guard_tool(_agent({"policy_gate": gate}), "recall_memory", {"query": "x"})
    assert gate.calls
    assert "确认" in out


def test_gate_allow():
    gate = _FakeGate("allow")
    out, cap = _guard_tool(_agent({"policy_gate": gate}), "recall_memory", {"query": "x"})
    assert gate.calls
    assert out is None and isinstance(cap, dict)


def test_gate_fail_closed():
    # 无动作评估器的 gate → check_action 返回 raw=None(fail-closed)→ 必须拒绝而非放行
    out, cap = _guard_tool(_agent({"policy_gate": PolicyGate()}), "recall_memory", {"query": "x"})
    assert "fail-closed" in out


def test_no_gate_fallback():
    # 无 gate(standalone)→ fallback 直接 permission.evaluate,read_only 工具放行
    out, cap = _guard_tool(_agent(None), "recall_memory", {"query": "x"})
    assert out is None
