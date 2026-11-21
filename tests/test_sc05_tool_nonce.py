"""SC-05·工具路径 confirm_nonce 核验与守护。

核验结论:默认工具集唯一工具 recall_memory 为 read,不构成 critical(send/pay/destructive),
当前工具路径无 nonce 触发点,亦无「绕过必要确认」的可能。

守护契约(文档:新增 Desktop 高风险操作必须在其自身生产调用链接入确认机制):
1. 默认工具集不得出现 critical 工具(出现即有人加高风险工具而未接 nonce,应失败);
2. 即便 future 出现 critical 动作,闸门也只 HITL,绝不 allow 静默执行。
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from foxclaw.core import tools
from foxclaw.core.confirm_nonce import is_critical
from foxclaw.agents.mixins.tool_exec import _guard_tool


def test_registry_has_no_critical_tool():
    """SC-05 核验:默认工具集无 critical 工具,当前工具路径无 nonce 触发点。"""
    for name in tools.default_registry().names():
        cap = tools.tool_capability(name)
        assert not is_critical(cap.get("side_effect", "read"),
                               bool(cap.get("destructive"))), name


def test_recall_memory_is_read_noncritical():
    """SC-05 核验:唯一工具 recall_memory 为只读,不应为补齐形式扩大 nonce 改造。"""
    cap = tools.tool_capability("recall_memory")
    assert cap.get("side_effect") == "read" and cap.get("read_only")
    assert not is_critical(cap.get("side_effect", ""), bool(cap.get("destructive")))


@pytest.mark.parametrize("cap", [
    {"side_effect": "send", "destructive": False},
    {"side_effect": "pay", "destructive": False},
    {"side_effect": "read", "destructive": True},
])
def test_tool_path_critical_action_hitl_not_silent(monkeypatch, cap):
    """SC-05 守护:工具路径若承载 critical 动作,闸门必须 HITL,绝不 allow 静默执行。"""
    monkeypatch.setattr(tools, "tool_capability",
                        lambda name: {"read_only": False, "external": True,
                                      "parallel_safe": False, **cap})
    agent = types.SimpleNamespace(_think=lambda **k: None)
    out, _ = _guard_tool(agent, "recall_memory", {"query": "x"})
    assert out is not None, "critical 动作不得放行(返回 None 即 allow 静默执行)"
    assert "确认" in out
