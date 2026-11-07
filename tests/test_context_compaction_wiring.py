"""上下文压缩接线回归。

背景:`core/compaction.py` 的 Compactor 实现完整,但长期**零生产调用点**——
实际生效的只有 `_sanitize_history` 的「按条数砍到 12 条」,长对话/多工具轮
会直接把 provider 上下文撑爆。本文件锁住三件事:

1. `Compactor.maybe_compact` 的契约(未超预算不产结果、超预算真变小、原样兜底);
2. `CognitiveAgent._compact_context` 的接入行为(可用 env 关、异常不外泄);
3. 工具调用循环里**确实**每轮都过压缩闸(`_reply_with_tools`)。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from foxclaw.agents.base import CognitiveAgent
from foxclaw.agents.mixins.tool_exec import ToolExecMixin
from foxclaw.core.compaction import Compactor, estimate_messages_tokens


def _long_history(n: int = 12, chars: int = 400) -> list:
    return [{"role": ("user" if i % 2 == 0 else "assistant"),
             "content": f"第{i}轮:" + ("内容" * (chars // 2))}
            for i in range(n)]


# ── 1. Compactor.maybe_compact 契约 ─────────────────────────────

def test_maybe_compact_noop_when_under_budget() -> None:
    msgs = [{"role": "user", "content": "短问题"}]
    out, res = Compactor(router=None, max_tokens=6000).maybe_compact(msgs)
    assert res is None, "未超预算不该产生 CompactionResult"
    assert out == msgs


def test_maybe_compact_reduces_tokens_over_budget() -> None:
    msgs = _long_history()
    before = estimate_messages_tokens(msgs)
    out, res = Compactor(router=None, max_tokens=100).maybe_compact(msgs)
    assert res is not None
    assert res.method == "truncate", "无 router 时应走截断兜底"
    assert estimate_messages_tokens(out) < before, "压缩后必须真的更小"
    assert out != msgs


def test_maybe_compact_keeps_system_and_recent() -> None:
    msgs = [{"role": "system", "content": "系统提示"}] + _long_history()
    out, _ = Compactor(router=None, max_tokens=100).maybe_compact(msgs)
    assert out[0].get("role") == "system", "系统提示必须保留"
    assert any(m.get("role") == "assistant" for m in out), "近期消息应保留"


def test_maybe_compact_never_raises_on_garbage() -> None:
    out, res = Compactor(router=None, max_tokens=1).maybe_compact([{"role": None}])
    assert isinstance(out, list)


# ── 2. CognitiveAgent._compact_context 接入层 ────────────────────────

def _stub_agent(thoughts: list) -> SimpleNamespace:
    return SimpleNamespace(router=None,
                           _think=lambda **kw: thoughts.append(kw.get("content", "")))


def test_compact_context_applies_and_records_thought(monkeypatch) -> None:
    monkeypatch.setenv("FOXCLAW_CONTEXT_MAX_TOKENS", "100")
    thoughts: list = []
    msgs = _long_history()
    out = CognitiveAgent._compact_context(_stub_agent(thoughts), msgs, "chat")
    assert estimate_messages_tokens(out) < estimate_messages_tokens(msgs)
    assert any("上下文压缩[chat]" in t for t in thoughts), "压缩后应留可观测痕迹"


def test_compact_context_noop_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("FOXCLAW_CONTEXT_MAX_TOKENS", "0")
    msgs = _long_history()
    out = CognitiveAgent._compact_context(_stub_agent([]), msgs, "chat")
    assert out is msgs, "预算置 0 时应整体关闭,原样返回"


def test_compact_context_swallows_errors(monkeypatch) -> None:
    monkeypatch.setenv("FOXCLAW_CONTEXT_MAX_TOKENS", "100")
    msgs = _long_history()
    out = CognitiveAgent._compact_context(SimpleNamespace(router=None), msgs, "chat")
    assert isinstance(out, list)          # _think 缺失 → 内部异常被吞,不影响主流程


# ── 3. 工具调用循环里确实过闸 ───────────────────────────────────

class _NoToolRouter:
    """supports_tools=True 但模型直接给答案:只跑一轮,便于断言压缩闸被调用。"""

    def supports_tools(self) -> bool:
        return True

    def call_tools(self, system, messages, schemas, tool_choice=None, agent_id=None) -> dict:
        return {"supported": True, "tool_calls": [], "text": "好的"}


class _RecordingAgent(ToolExecMixin):
    AGENT_ID = "test"

    def __init__(self) -> None:
        self.router = _NoToolRouter()
        self.system_prompt = "sys"
        self.stages: list = []

    def _compact_context(self, messages, stage: str = "") -> list:
        self.stages.append(stage)
        return messages

    def _think(self, **kwargs) -> None:
        return


def test_reply_with_tools_compacts_each_round() -> None:
    agent = _RecordingAgent()
    out = agent._reply_with_tools("任务", [{"role": "user", "content": "hi"}])
    assert out == "好的"
    assert agent.stages == ["tool_loop"], "工具轮每轮都应过上下文压缩闸"


@pytest.mark.parametrize("limit", ["6000", "0"])
def test_context_env_parsing(monkeypatch, limit) -> None:
    monkeypatch.setenv("FOXCLAW_CONTEXT_MAX_TOKENS", limit)
    msgs = [{"role": "user", "content": "x"}]
    out = CognitiveAgent._compact_context(_stub_agent([]), msgs, "chat")
    assert isinstance(out, list)
