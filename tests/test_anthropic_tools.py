"""
P0-2 anthropic tool_use 原生接入测试 —— 全离线(fake anthropic client,不打真实网络)。

覆盖:
  1. _to_anthropic_tools:openai tools 格式 → anthropic 格式
  2. _to_anthropic_tool_choice:auto/required/指定工具
  3. supports_tools:anthropic 纳入支持(有 client 时)
  4. call_tools anthropic 分支:tool_use block 解析(结构化 input 无需 JSON 解析)
  5. call_tools anthropic:纯文本回答(text block)
  6. tool_choice=none → 不传 tools,文本收敛
  7. format_tool_result anthropic:tool_result block
  8. fail-safe:fake client 抛异常 → supported=False;无 client(stub)→ supported=False
  9. 回灌闭环:_reply_with_tools 用 anthropic 格式 assistant_msg/tool_result
"""
import os, sys, types
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_EMBEDDING_MODEL"] = "foxclaw-nonexistent-fast"
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.core.model_router import ModelRouter


def _block(**kw):
    return types.SimpleNamespace(**kw)


def _fake_client(script):
    """fake anthropic client:client.messages.create(**kw) → 按脚本返回 content blocks。"""
    c = types.SimpleNamespace()
    c.calls = []
    c.script = list(script)

    def create(**kwargs):
        c.calls.append(kwargs)
        return types.SimpleNamespace(content=list(c.script.pop(0)))

    c.messages = types.SimpleNamespace(create=create)
    return c


def _router(client):
    """用 __new__ 构造 anthropic ModelRouter(绕过 __init__ 的 SDK/网络探测)。"""
    r = ModelRouter.__new__(ModelRouter)
    r.provider = "anthropic"
    r.model = "claude-sonnet-4-20250514"
    r._locked_model = ""
    r._client = client
    r._cooldown = {}
    r.meter = None
    r.fallback_reason = ""
    r.degraded = False
    r.degraded_reason = ""
    r._call_degraded = False
    return r


def _plan_tool():
    return {"type": "function",
            "function": {"name": "submit_plan", "description": "d",
                         "parameters": {"type": "object",
                                        "properties": {"flow": {"type": "string"}},
                                        "required": ["flow"]}}}


# ── 1. 格式转换 ───────────────────────────────────────────────────────────────
def test_to_anthropic_tools():
    out = ModelRouter._to_anthropic_tools([
        _plan_tool(),
        {"name": "direct", "description": "", "input_schema": {}},   # 已是 anthropic 格式
    ])
    assert len(out) == 2
    assert out[0]["name"] == "submit_plan"
    assert out[0]["input_schema"]["required"] == ["flow"]
    assert out[1]["name"] == "direct"
    # 非法项(空名/非 dict)跳过
    assert ModelRouter._to_anthropic_tools([{"type": "function", "function": {"name": ""}}]) == []
    assert ModelRouter._to_anthropic_tools(None) == []


def test_to_anthropic_tool_choice():
    assert ModelRouter._to_anthropic_tool_choice("auto") == {"type": "auto"}
    assert ModelRouter._to_anthropic_tool_choice(None) == {"type": "auto"}
    assert ModelRouter._to_anthropic_tool_choice("none") == {"type": "none"}
    assert ModelRouter._to_anthropic_tool_choice("required") == {"type": "any"}
    assert ModelRouter._to_anthropic_tool_choice(
        {"type": "function", "function": {"name": "x"}}) == {"type": "tool", "name": "x"}


# ── 2. supports_tools ──────────────────────────────────────────────────────────
def test_supports_tools_anthropic():
    r = _router(_fake_client([[]]))
    assert r.is_stub is False
    assert r.supports_tools() is True
    # 无 client(stub)→ 不支持
    r2 = ModelRouter.__new__(ModelRouter)
    r2.provider = "anthropic"; r2._client = None
    assert r2.supports_tools() is False


# ── 3. call_tools anthropic 分支 ──────────────────────────────────────────────
def test_call_tools_anthropic_tool_use():
    client = _fake_client([[
        _block(type="tool_use", id="toolu_1", name="submit_plan",
               input={"flow": "sequential",
                      "steps": [{"name": "查支出", "agent": "finance"}]}),
    ]])
    r = _router(client)
    res = r.call_tools("sys", [{"role": "user", "content": "t"}], [_plan_tool()])
    assert res["supported"] is True
    assert res["tool_calls"] == [{"id": "toolu_1", "name": "submit_plan",
                                  "args": {"flow": "sequential",
                                           "steps": [{"name": "查支出", "agent": "finance"}]},
                                  "args_parse_failed": False}]
    # 入参:tools 转 anthropic 格式 + tool_choice 转 auto + system 单独传 + 运行时注入
    kw = client.calls[0]
    assert kw["system"].startswith("【运行时信息") and "sys" in kw["system"]
    assert kw["tools"][0]["name"] == "submit_plan"
    assert kw["tool_choice"] == {"type": "auto"}
    assert all(m["role"] != "system" for m in kw["messages"]), "anthropic 不允许 system 角色入 messages"
    # assistant_msg 是 anthropic 原生 blocks
    assert res["assistant_msg"]["role"] == "assistant"
    assert res["assistant_msg"]["content"][0]["type"] == "tool_use"


def test_call_tools_anthropic_text_only():
    client = _fake_client([[_block(type="text", text="你好,无需工具。")]])
    r = _router(client)
    res = r.call_tools("sys", [{"role": "user", "content": "hi"}], [])
    assert res["text"] == "你好,无需工具。"
    assert res["tool_calls"] == []
    assert res["assistant_msg"]["content"] == [{"type": "text", "text": "你好,无需工具。"}]


def test_call_tools_anthropic_tool_choice_none():
    client = _fake_client([[_block(type="text", text="收敛答案")]])
    r = _router(client)
    res = r.call_tools("sys", [{"role": "user", "content": "hi"}], [_plan_tool()],
                       tool_choice="none")
    assert res["text"] == "收敛答案"
    kw = client.calls[0]
    assert kw["tool_choice"] == {"type": "none"}, "none 映射为 {type:none}"
    assert kw["tools"][0]["name"] == "submit_plan", "历史含 tool_use 时必须仍传 tools"


# ── 4. format_tool_result ─────────────────────────────────────────────────────
def test_format_tool_result_anthropic():
    r = _router(_fake_client([[]]))
    out = r.format_tool_result({"id": "toolu_9", "name": "web_search"}, "结果")
    assert out == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_9", "content": "结果"}]}


# ── 5. fail-safe ──────────────────────────────────────────────────────────────
def test_call_tools_anthropic_failsafe():
    def create(**kw):
        raise RuntimeError("boom")
    client = types.SimpleNamespace()
    client.messages = types.SimpleNamespace(create=create)
    r = _router(client)
    res = r.call_tools("sys", [{"role": "user", "content": "t"}], [])
    assert res["supported"] is False and res.get("error")

    # stub(无 client)→ supported=False
    r2 = _router(None)
    res2 = r2.call_tools("sys", [{"role": "user", "content": "t"}], [])
    assert res2["supported"] is False


# ── 6. 回灌闭环(_reply_with_tools 在 anthropic 下)────────────────────────────
def test_reply_with_tools_anthropic_loop():
    from foxclaw.agents.base import CognitiveAgent
    client = _fake_client([
        # 第1轮:模型要求调 recall_memory
        [_block(type="tool_use", id="c1", name="recall_memory", input={"query": "偏好"})],
        # 第2轮:拿到结果后给最终答案
        [_block(type="text", text="根据记忆,你喜欢灰色。")],
    ])
    r = _router(client)
    agent = types.SimpleNamespace()
    agent.router = r
    agent.system_prompt = "sys"
    agent.AGENT_ID = "assistant"
    agent._dispatch_tool = lambda name, args: f"RESULT[{name}:{args.get('query', '')}]"

    out = CognitiveAgent._reply_with_tools(
        agent, "我喜欢什么颜色", [{"role": "user", "content": "我喜欢什么颜色"}])
    assert out == "根据记忆,你喜欢灰色。"
    # 第2轮的 messages 必须含 anthropic 格式 assistant(tool_use) + user(tool_result)
    kw2 = client.calls[1]
    has_tool_result = any(
        isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_result" for b in m["content"])
        for m in kw2["messages"])
    assert has_tool_result, "回灌必须用 anthropic tool_result block"
    has_tool_use = any(
        isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_use" for b in m["content"])
        for m in kw2["messages"])
    assert has_tool_use, "回灌必须含 anthropic assistant tool_use block"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
