"""
数据污点追踪 + 动态审批门槛测试 —— 全离线。

对齐 Meta Muse「eBPF 污点追踪」的软件版:会话一旦取回用户/敏感数据就打污点,
之后外发/副作用动作的审批门槛动态提高。覆盖:
  1. injection_guard.taint_level:来源 → 敏感度分级。
  2. permission.evaluate 污点门槛:默认关闭(行为不变)/ 达标+外发 → TAINT_CONFIRM /
     达标+只读 → 不触发 / 未达标 → 不触发。
  3. base 污点传播:_dispatch_tool 取回 memory/web_search 后会话污点单调上升;
     会话开始 _taint_reset 清零。
"""
import os, sys, types
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_EMBEDDING_MODEL"] = "foxclaw-nonexistent-fast"
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.core import injection_guard as IG
from foxclaw.skills import permission as PERM
from foxclaw.agents.base import (
    CognitiveAgent, _taint_get, _taint_raise, _taint_reset,
)


def _skill(side_effect="read", read_only=True, external=False, destructive=False):
    """构造一个 permission.evaluate 可用的最小 skill-spec(duck-typed)。"""
    return types.SimpleNamespace(
        id=f"tool:test_{side_effect}",
        params={},
        side_effect=side_effect,
        read_only=read_only,
        external=external,
        destructive=destructive,
        trust_level=lambda: "high",
    )


# ── 1. taint_level 分级 ───────────────────────────────────────────────
def test_taint_level_grading():
    assert IG.taint_level("web_search") == IG.TAINT_EXTERNAL == 1
    assert IG.taint_level("knowledge") == IG.TAINT_PERSONAL == 2
    assert IG.taint_level("file") == IG.TAINT_PERSONAL == 2
    assert IG.taint_level("memory") == IG.TAINT_SENSITIVE == 3
    assert IG.taint_level("unknown_source") == IG.TAINT_EXTERNAL   # 未知来源不低估


# ── 2. permission 污点门槛 ─────────────────────────────────────────────
def test_permission_taint_default_off_behavior_unchanged():
    # 不传污点参数(默认关闭)→ 外发工具走原有 Stage 4 副作用确认
    s = _skill(side_effect="send", read_only=False)
    d = PERM.evaluate(s, {})
    assert d.decision == "ask" and d.reason_code == PERM.SIDE_EFFECT_CONFIRM


def test_permission_taint_blocks_send_when_sensitive():
    s = _skill(side_effect="send", read_only=False)
    d = PERM.evaluate(s, {}, taint_level=IG.TAINT_SENSITIVE,
                      taint_threshold=IG.TAINT_PERSONAL)
    assert d.decision == "ask" and d.reason_code == PERM.TAINT_CONFIRM
    assert d.requires_confirmation


def test_permission_taint_blocks_external_when_personal():
    s = _skill(side_effect="read", read_only=False, external=True)
    d = PERM.evaluate(s, {}, taint_level=IG.TAINT_PERSONAL,
                      taint_threshold=IG.TAINT_PERSONAL)
    assert d.decision == "ask" and d.reason_code == PERM.TAINT_CONFIRM


def test_permission_taint_not_trigger_on_readonly():
    s = _skill(side_effect="read", read_only=True)
    d = PERM.evaluate(s, {}, taint_level=IG.TAINT_SENSITIVE,
                      taint_threshold=IG.TAINT_PERSONAL)
    assert d.decision == "allow"


def test_permission_taint_not_trigger_below_threshold():
    s = _skill(side_effect="send", read_only=False)
    # 污点只有 EXTERNAL(1) < 阈值 PERSONAL(2)→ 不因污点拦,走副作用确认
    d = PERM.evaluate(s, {}, taint_level=IG.TAINT_EXTERNAL,
                      taint_threshold=IG.TAINT_PERSONAL)
    assert d.reason_code == PERM.SIDE_EFFECT_CONFIRM


# ── 3. base 污点传播 ───────────────────────────────────────────────────
def test_taint_helpers_on_fake_agent():
    a = types.SimpleNamespace(_think=lambda **k: None)
    assert _taint_get(a) == 0
    _taint_raise(a, IG.TAINT_EXTERNAL)
    _taint_raise(a, IG.TAINT_SENSITIVE)
    assert _taint_get(a) == IG.TAINT_SENSITIVE    # 单调不减
    _taint_reset(a)
    assert _taint_get(a) == 0


def test_dispatch_recall_memory_raises_taint():
    a = types.SimpleNamespace(
        _think=lambda **k: None,
        _recall_memory=lambda q, top_k=5: [{"content": "用户偏好:喜欢简洁"}])
    out = CognitiveAgent._dispatch_tool(a, "recall_memory", {"query": "偏好"})
    assert "喜欢简洁" in out
    assert _taint_get(a) == IG.TAINT_SENSITIVE


def test_dispatch_web_search_raises_taint(monkeypatch):
    from foxclaw.core import web_search as WS
    monkeypatch.setattr(WS, "search_answer", lambda q: {"answer": "答案ABC", "references": []})
    a = types.SimpleNamespace(_think=lambda **k: None)
    out = CognitiveAgent._dispatch_tool(a, "web_search", {"query": "问题"})
    assert "答案ABC" in out
    assert _taint_get(a) == IG.TAINT_EXTERNAL


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
