"""
P2 function-calling(工具化)守卫测试 —— 全离线,不打真实 LLM/网络。

覆盖:
  1. P1 运行时事实基准注入(_runtime_context / _inject_runtime 幂等)。
  2. ToolRegistry:默认工具集、deny 过滤、tool_use 开关读 env。
  3. ModelRouter.call_tools:不支持 tools 的 provider → supported=False(降级)。
  4. _reply_with_tools:多轮工具循环(调用→回灌→最终答);不支持 → None(fallback)。
  5. _dispatch_tool:web_search 命中 / 个人数据否决 / recall_memory / deny 闸。
"""
import os, sys, types
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_EMBEDDING_MODEL"] = "foxclaw-nonexistent-fast"
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.core import tools as T
from foxclaw.core.model_router import ModelRouter
from foxclaw.agents.base import CognitiveAgent


# ── 1. P1 运行时注入 ───────────────────────────────────────────────────────────
def test_runtime_context_and_idempotent():
    r = ModelRouter(provider="stub")
    ctx = r._runtime_context()
    assert "运行时信息" in ctx and "模型" in ctx and "当前日期时间" in ctx
    a = r._inject_runtime("你是助手。")
    assert a.startswith("【运行时信息")
    assert r._inject_runtime(a) == a, "二次注入必须幂等(防 stream→complete 重复)"


# ── 2. ToolRegistry ────────────────────────────────────────────────────────────
def test_registry_default_and_deny():
    reg = T.default_registry()
    assert set(reg.names()) == {"web_search", "recall_memory"}
    names = [s["function"]["name"] for s in reg.schemas()]
    assert "web_search" in names and "recall_memory" in names
    os.environ["FOXCLAW_TOOL_DENY"] = "web_search"
    try:
        names2 = [s["function"]["name"] for s in reg.schemas()]
        assert "web_search" not in names2 and "recall_memory" in names2
        assert T.is_tool_denied("web_search")
    finally:
        os.environ.pop("FOXCLAW_TOOL_DENY", None)


def test_tool_use_flag_default_off():
    os.environ.pop("FOXCLAW_TOOL_USE", None)
    assert T.tool_use_enabled() is False, "默认必须关(behavior-preserving)"
    os.environ["FOXCLAW_TOOL_USE"] = "1"
    try:
        assert T.tool_use_enabled() is True
    finally:
        os.environ.pop("FOXCLAW_TOOL_USE", None)


# ── 3. call_tools 降级 ─────────────────────────────────────────────────────────
def test_call_tools_unsupported_degrades():
    r = ModelRouter(provider="stub")
    assert r.supports_tools() is False
    res = r.call_tools("sys", [{"role": "user", "content": "hi"}], [])
    assert res["supported"] is False and res["tool_calls"] == []


# ── 4. 工具循环 ────────────────────────────────────────────────────────────────
class _FakeRouter:
    """脚本化 call_tools:按序返回预设结果,记录每轮 messages 长度。"""
    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def supports_tools(self):
        return True

    def call_tools(self, system, messages, schemas, tool_choice="auto", agent_id=""):
        self.seen.append((tool_choice, list(messages)))
        return self.script.pop(0)

    @staticmethod
    def format_tool_result(tc, result):
        return {"role": "tool", "tool_call_id": tc.get("id", ""), "content": str(result)}


def _fake_agent(router):
    a = types.SimpleNamespace()
    a.router = router
    a.system_prompt = "sys"
    a.AGENT_ID = "assistant"
    a._dispatch_tool = lambda name, args: f"RESULT[{name}:{args.get('query','')}]"
    return a


def test_reply_with_tools_multi_round():
    router = _FakeRouter([
        # 第1轮:模型要求调 web_search
        {"supported": True, "text": "", "assistant_msg": {"role": "assistant", "content": ""},
         "tool_calls": [{"id": "c1", "name": "web_search", "args": {"query": "最新模型"}}]},
        # 第2轮:拿到结果后给最终答案
        {"supported": True, "text": "根据搜索,最新是 X。", "tool_calls": [],
         "assistant_msg": {"role": "assistant", "content": "根据搜索,最新是 X。"}},
    ])
    agent = _fake_agent(router)
    out = CognitiveAgent._reply_with_tools(agent, "有哪些新模型",
                                           [{"role": "user", "content": "有哪些新模型"}])
    assert out == "根据搜索,最新是 X。"
    # 第2轮的 messages 必须已追加 assistant(带 tool_calls)+ tool 结果
    _, round2_msgs = router.seen[1]
    roles = [m["role"] for m in round2_msgs]
    assert "assistant" in roles and "tool" in roles
    assert any("RESULT[web_search" in str(m.get("content", "")) for m in round2_msgs)


def test_reply_with_tools_direct_answer_no_tool():
    router = _FakeRouter([
        {"supported": True, "text": "你好呀!", "tool_calls": [],
         "assistant_msg": {"role": "assistant", "content": "你好呀!"}},
    ])
    agent = _fake_agent(router)
    out = CognitiveAgent._reply_with_tools(agent, "你好", [{"role": "user", "content": "你好"}])
    assert out == "你好呀!" and len(router.seen) == 1


def test_reply_with_tools_unsupported_returns_none():
    agent = types.SimpleNamespace(router=types.SimpleNamespace(supports_tools=lambda: False),
                                  system_prompt="s", AGENT_ID="a")
    out = CognitiveAgent._reply_with_tools(agent, "t", [{"role": "user", "content": "t"}])
    assert out is None, "不支持 tools 必须返回 None 让上层 fallback"


def test_reply_with_tools_empty_answer_returns_none():
    # 阶段五·步骤15:模型无工具调用且文本为空 → 返回 None 让上层 fallback(不再"空回复且不回退")
    router = _FakeRouter([
        {"supported": True, "text": "", "tool_calls": [],
         "assistant_msg": {"role": "assistant", "content": ""}},
    ])
    agent = _fake_agent(router)
    out = CognitiveAgent._reply_with_tools(agent, "你好", [{"role": "user", "content": "你好"}])
    assert out is None, "空答必须返回 None 让上层 fallback"


# ── 5. 工具分派 ────────────────────────────────────────────────────────────────
def test_dispatch_web_search_ok(monkeypatch):
    from foxclaw.core import web_search as ws
    monkeypatch.setattr(ws, "search_answer", lambda q: {"answer": "实时答案XYZ", "references": []})
    agent = types.SimpleNamespace(_think=lambda **k: None)
    out = CognitiveAgent._dispatch_tool(agent, "web_search", {"query": "OpenAI 新模型"})
    assert "实时答案XYZ" in out


def test_dispatch_web_search_personal_veto():
    agent = types.SimpleNamespace(_think=lambda **k: None)
    out = CognitiveAgent._dispatch_tool(agent, "web_search", {"query": "我本月支出多少"})
    assert "本地" in out, "私人财务问题不得外发联网"


def test_dispatch_recall_memory():
    agent = types.SimpleNamespace(
        _recall_memory=lambda q, top_k=5: [{"content": "用户喜欢灰色"}])
    out = CognitiveAgent._dispatch_tool(agent, "recall_memory", {"query": "颜色偏好"})
    assert "灰色" in out


def test_dispatch_denied(monkeypatch):
    monkeypatch.setenv("FOXCLAW_TOOL_DENY", "web_search")
    agent = types.SimpleNamespace(_think=lambda **k: None)
    out = CognitiveAgent._dispatch_tool(agent, "web_search", {"query": "x"})
    assert "禁用" in out


# ── 6. 动作级权限闸(permission 管线 + HITL)──────────────────────────────────────
def test_tool_capability_readonly_and_failclosed():
    assert T.tool_capability("web_search")["read_only"] is True
    assert T.tool_capability("recall_memory")["side_effect"] == "read"
    # 未声明的新工具 → fail-closed:当有副作用 + 外部
    cap = T.tool_capability("some_future_write_tool")
    assert cap["read_only"] is False and cap["side_effect"] == "write" and cap["external"] is True


def test_permission_gate_allows_readonly_asks_sideeffect():
    import types as _t
    from foxclaw.skills import permission as perm
    ro = _t.SimpleNamespace(id="tool:web_search", params={}, **T.tool_capability("web_search"))
    assert perm.evaluate(ro, {}, confirmed=False).decision == "allow"
    send = _t.SimpleNamespace(id="tool:send_email", params={},
                              side_effect="send", read_only=False, external=True, destructive=False)
    d = perm.evaluate(send, {}, confirmed=False)
    assert d.decision == "ask" and d.requires_confirmation


def test_dispatch_hitl_blocks_side_effect_tool(monkeypatch):
    """核心:若工具被判为有副作用(写/发送),_dispatch_tool 走 HITL——不执行、返回需确认。"""
    from foxclaw.core import web_search as ws
    called = {"n": 0}
    monkeypatch.setattr(ws, "search_answer",
                        lambda q: called.__setitem__("n", called["n"] + 1) or {"answer": "x"})
    # 把 web_search 强制降级为有副作用 → 应被闸门拦成 HITL
    monkeypatch.setattr(T, "tool_capability",
                        lambda name: {"side_effect": "send", "read_only": False,
                                      "external": True, "destructive": False})
    agent = types.SimpleNamespace(_think=lambda **k: None)
    out = CognitiveAgent._dispatch_tool(agent, "web_search", {"query": "x"})
    assert "确认" in out and called["n"] == 0, "有副作用工具必须 HITL、不自动执行"


def test_dispatch_unknown_tool():
    agent = types.SimpleNamespace(_think=lambda **k: None)
    out = CognitiveAgent._dispatch_tool(agent, "nonexistent_tool", {"x": 1})
    assert "未知工具" in out


def test_tool_path_injects_preferences(monkeypatch):
    """回归(Finding 1):工具路径也要注入用户偏好,不能开工具化就丢掉"越用越懂你"。"""
    monkeypatch.setenv("FOXCLAW_TOOL_USE", "1")
    captured = {}
    fake = types.SimpleNamespace()
    fake._sanitize_history = lambda h, max_turns=12: []
    fake.router = types.SimpleNamespace(supports_tools=lambda: True)
    fake.AGENT_ID = "assistant"
    fake._ability_augment = lambda t, m: m
    fake._skill_augment = lambda t, m: m
    # 偏好注入:把标记并进最后一条 user
    fake._pref_augment = lambda m: (list(m[:-1]) +
        [{"role": "user", "content": "【用户偏好】喜欢简洁\n\n" + m[-1]["content"]}])

    def _rwt(task, messages, stop_event=None):
        captured["msgs"] = messages
        return "好的,已处理。"
    fake._reply_with_tools = _rwt
    fake._think = lambda **k: None
    fake._store_memory = lambda *a, **k: None
    fake._maybe_store_preference = lambda t: None

    out = "".join(CognitiveAgent.stream_reply(fake, "帮我总结这段"))
    assert "已处理" in out
    assert any("喜欢简洁" in str(x.get("content", "")) for x in captured["msgs"]), \
        "工具路径必须注入用户偏好(Finding 1)"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
