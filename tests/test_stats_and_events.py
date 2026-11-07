"""
记忆统计 + 活动事件 回归测试(全离线)。

覆盖两个实锤 bug 的修复:
  1. hmr.stats() 从 vector_store 探测 count/size/memories 取 total —— 但真实记忆在 memory_fs,
     vector_store 只存嵌入且无这些属性 → total 恒为 0(而 relations 却有值,自相矛盾)。
     修复:改为数 memory_fs.list_memories();by_agent 从 tags 里的 "agent:<id>" 解析。
  2. 对话路径(stream_reply)此前不发任何事件 → /bus/history 恒空、前端"活动"页永远没内容。
     修复:对话完成后 publish 一条 chat.completed。
"""
import os, sys, types, threading
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_EMBEDDING_MODEL"] = "foxclaw-nonexistent-fast"
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.core.memory_adapter import HMRAdapter
from foxclaw.agents.base import CognitiveAgent


def _mem(tags):
    return types.SimpleNamespace(tags=list(tags))


def _adapter_with(hmr_obj):
    """构造最小 self 调用 HMRAdapter.stats(不加载真实 HMR)。"""
    s = types.SimpleNamespace()
    s._lock = threading.RLock()
    s._hmr = hmr_obj
    s.version = "2.0.0"
    s.backend = "HMR"
    return s


# ── 1. 记忆统计 ────────────────────────────────────────────────────────────────
def test_stats_counts_from_memory_fs_not_vector_store():
    """核心回归:记忆在 memory_fs;vector_store 没有 count/size/memories 也不能让 total 归零。"""
    mems = [_mem(["agent:supervisor"]), _mem(["agent:supervisor"]),
            _mem(["agent:finance", "shared"]), _mem(["agent:office"])]
    hmr = types.SimpleNamespace(
        memory_fs=types.SimpleNamespace(list_memories=lambda: mems),
        vector_store=types.SimpleNamespace(),      # 故意:没有 count/size/memories(复现旧 bug 条件)
        memory_graph=None)
    out = HMRAdapter.stats(_adapter_with(hmr))
    assert out["total_memories"] == 4, "必须从 memory_fs 数出真实记忆数"
    assert out["by_agent"] == {"supervisor": 2, "finance": 1, "office": 1}
    assert out["shared"] == 1 and out["private"] == 3


def test_stats_falls_back_to_vector_store_vectors():
    """memory_fs 不可用时,兜底数 vector_store.vectors(dict)。"""
    hmr = types.SimpleNamespace(
        memory_fs=None,
        vector_store=types.SimpleNamespace(vectors={"a": 1, "b": 2, "c": 3}),
        memory_graph=None)
    out = HMRAdapter.stats(_adapter_with(hmr))
    assert out["total_memories"] == 3


def test_stats_relations_from_graph_unchanged():
    """relations 仍来自 memory_graph.edges(这条原本就是对的,不能改坏)。"""
    hmr = types.SimpleNamespace(
        memory_fs=types.SimpleNamespace(list_memories=lambda: []),
        vector_store=None,
        memory_graph=types.SimpleNamespace(edges=[1, 2, 3, 4, 5]))
    out = HMRAdapter.stats(_adapter_with(hmr))
    assert out["relations"] == 5


def test_stats_survives_broken_hmr():
    """HMR 结构异常时不抛,返回零值(fail-safe,前端不白屏)。"""
    hmr = types.SimpleNamespace(memory_fs=None, vector_store=None, memory_graph=None)
    out = HMRAdapter.stats(_adapter_with(hmr))
    assert out["total_memories"] == 0 and out["relations"] == 0


# ── 1b. 记忆标题可读性 ─────────────────────────────────────────────────────────
def test_derive_title_from_content():
    d = HMRAdapter._derive_title
    assert d("对话:你好 → 你好呀！周五中午,正是一周里…") == "你好"
    assert d("用户偏好:我喜欢简洁的回答") == "我喜欢简洁的回答"
    assert d("把这份报告存入HMR → 好的,我现在") == "把这份报告存入HMR"
    assert d("") == ""
    assert len(d("很长" * 50)) <= 24          # 限长


def test_recent_replaces_timestamp_titles():
    """HMR 默认标题是 "Concept 03:11"(纯时间戳)→ 展示时必须现推可读标题(老数据也要好看)。"""
    m = types.SimpleNamespace(id="m1", type="concept", title="Concept 03:11",
                              semantic_summary="", content="对话:今天是几号 → 今天是 7 月 24 日",
                              tags=["agent:supervisor"], created_at=None)
    s = HMRAdapter.__new__(HMRAdapter)
    s._lock = threading.RLock(); s.version = "2.0.0"; s.backend = "HMR"
    s._hmr = types.SimpleNamespace(memory_fs=types.SimpleNamespace(list_memories=lambda: [m]))
    out = s.recent(5)
    assert out[0]["title"] == "今天是几号", "时间戳标题必须被替换成可读标题"
    assert out[0]["agent"] == "supervisor"


def test_recent_keeps_meaningful_title():
    """已有可读标题的不许被覆盖。"""
    m = types.SimpleNamespace(id="m2", type="concept", title="季度财务复盘",
                              semantic_summary="", content="随便什么内容",
                              tags=[], created_at=None)
    s = HMRAdapter.__new__(HMRAdapter)
    s._lock = threading.RLock(); s.version = "2.0.0"; s.backend = "HMR"
    s._hmr = types.SimpleNamespace(memory_fs=types.SimpleNamespace(list_memories=lambda: [m]))
    assert s.recent(5)[0]["title"] == "季度财务复盘"


# ── 2. 活动事件 ────────────────────────────────────────────────────────────────
def test_chat_publishes_activity_event(monkeypatch):
    """核心回归:对话完成后必须往事件脊柱发 chat.completed,否则"活动"页永远空。"""
    monkeypatch.setenv("FOXCLAW_TOOL_USE", "1")
    published = []
    spine = types.SimpleNamespace(
        publish=lambda topic, payload, source="": published.append((topic, payload, source)))

    fake = types.SimpleNamespace()
    fake._caps = {"event_spine": spine}
    fake._sanitize_history = lambda h, max_turns=12: []
    fake.router = types.SimpleNamespace(supports_tools=lambda: True)
    fake.AGENT_ID = "assistant"
    fake._ability_augment = lambda t, m: m
    fake._skill_augment = lambda t, m: m
    fake._pref_augment = lambda m: m
    fake._reply_with_tools = lambda task, messages, stop_event=None: "这是回复内容。"
    fake._think = lambda **k: None
    fake._store_memory = lambda *a, **k: None
    fake._maybe_store_preference = lambda t: None

    out = "".join(CognitiveAgent.stream_reply(fake, "帮我看看这个"))
    assert "回复内容" in out
    topics = [t for t, _, _ in published]
    assert "chat.completed" in topics, "对话路径必须发 chat.completed 事件"
    payload = [p for t, p, _ in published if t == "chat.completed"][0]
    assert payload["agent_id"] == "assistant"
    assert payload["reply_chars"] > 0
    assert payload["task"].startswith("帮我看看")


def test_chat_event_absent_without_spine():
    """无事件脊柱(standalone/治理场景)时静默跳过,不得抛错。"""
    fake = types.SimpleNamespace()
    fake._caps = None
    fake._sanitize_history = lambda h, max_turns=12: []
    fake.router = types.SimpleNamespace(supports_tools=lambda: False)
    fake.AGENT_ID = "assistant"
    fake._web_augment = lambda t, m: m
    fake._ability_augment = lambda t, m: m
    fake._skill_augment = lambda t, m: m
    fake._pref_augment = lambda m: m
    fake.system_prompt = "sys"
    fake.router.stream = lambda *a, **k: iter(["好", "的"])
    fake._think = lambda **k: None
    fake._store_memory = lambda *a, **k: None
    fake._maybe_store_preference = lambda t: None
    out = "".join(CognitiveAgent.stream_reply(fake, "你好"))
    assert out == "好的"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
