"""P0-03.1 CallStatus 语义验证(M01-M06 + 收集器)。

锁住:任务归属不串扰(不用 Router 全局布尔/Agent 实例字段)、故障不伪装成功、
流式中断不补 Stub 文本、Provider 三态区分 UNCONFIGURED 与 INIT_FAILED。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

from foxclaw.core.call_status import (  # noqa: E402
    CallStatus, bind_collector, current_collector,
    DEGRADED, FALLBACK, PENDING, REAL, STUB, UNSUPPORTED, reset_collector,
)
from foxclaw.core.model_router import ModelRouter  # noqa: E402


@pytest.fixture
def router(monkeypatch):
    """deepseek 单 provider、READY 的 Router;关掉 .env 热重载,客户端与调用全 mock。"""
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
              "FOXCLAW_PROVIDER", "FOXCLAW_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    r = ModelRouter(provider="deepseek", model="deepseek-chat")
    r._maybe_reload_selection = lambda: None
    r._get_client = lambda p: object() if p == "deepseek" else None
    return r


def _msgs():
    return [{"role": "user", "content": "hi"}]


# ---------- M01-M04: 四态 ----------

def test_m01_single_provider_success_is_real(router):
    router._call_provider = lambda *a: ("回答", None)
    st = CallStatus()
    assert router.complete("sys", _msgs(), status=st) == "回答"
    assert st.state == REAL and st.provider == "deepseek"
    assert st.terminal and st.attempted == ["deepseek"]


def test_m02_fallback_success_is_fallback(router, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "y")
    r = ModelRouter(provider="deepseek", model="deepseek-chat")
    r._maybe_reload_selection = lambda: None
    r._get_client = lambda p: object()
    calls = []

    def fake(p, *a):
        calls.append(p)
        if p == "deepseek":
            raise RuntimeError("down")
        return f"OK-{p}", None

    r._call_provider = fake
    st = CallStatus()
    out = r.complete("sys", _msgs(), status=st)
    assert "OK-openai" in out
    assert st.state == FALLBACK and st.provider == "openai"
    assert st.attempted == ["deepseek", "openai"], calls


def test_m03_all_fail_is_degraded(router):
    def boom(*a):
        raise RuntimeError("boom")

    router._call_provider = boom
    st = CallStatus()
    out = router.complete("sys", _msgs(), status=st)
    assert "全部模型失败" in out
    assert st.state == DEGRADED and "RuntimeError" in st.error
    assert "已完成处理" not in out, "故障被伪装成成功"


def test_m04_unconfigured_is_stub(monkeypatch):
    for k in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
              "GOOGLE_API_KEY", "FOXCLAW_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    r = ModelRouter()
    r._maybe_reload_selection = lambda: None
    assert r.provider_state == "UNCONFIGURED" and r.is_stub
    st = CallStatus()
    r.complete("sys", _msgs(), status=st)
    assert st.state == STUB and st.provider == "stub"


def test_init_failed_is_not_stub(monkeypatch):
    """显式指定 provider 但无 key → INIT_FAILED:进 fallback 链、不得伪装 Stub。"""
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
              "FOXCLAW_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    r = ModelRouter(provider="deepseek", model="deepseek-chat")
    r._maybe_reload_selection = lambda: None
    assert r.provider_state == "INIT_FAILED"
    assert not r.is_stub, "INIT_FAILED 不得算 stub"


# ---------- M05-M06: 流式 ----------

def _fake_stream(router, fail_p, partial_fail=False):
    def impl(provider, client, model, system, mm, messages, max_tokens,
             agent_id, parts, _stopped, on_thinking):
        if provider == fail_p:
            if partial_fail:
                parts.append("半截")
                yield "半截"
            raise RuntimeError("stream down")
        for w in ("你", "好"):
            parts.append(w)
            yield w
    router._stream_provider = impl


def test_m05_stream_fallback(router, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "y")
    r = ModelRouter(provider="deepseek", model="deepseek-chat")
    r._maybe_reload_selection = lambda: None
    r._get_client = lambda p: object()
    _fake_stream(r, fail_p="deepseek")
    st = CallStatus()
    out = "".join(r.stream("sys", _msgs(), status=st))
    assert out == "你好", out
    assert st.state == FALLBACK and st.provider == "openai"


def test_m06_stream_partial_degraded_no_stub_text(router):
    """已输出部分后失败:标 DEGRADED+partial,不补 Stub 占位文本。"""
    _fake_stream(router, fail_p="deepseek", partial_fail=True)
    st = CallStatus()
    out = "".join(router.stream("sys", _msgs(), status=st))
    assert out.startswith("半截")
    assert "【模型流式中断】" in out
    assert st.state == DEGRADED and st.partial is True
    assert "已完成处理" not in out, "中断被伪装成完整回复"


def test_stream_all_fail_no_stub_fake(router):
    _fake_stream(router, fail_p="deepseek")
    st = CallStatus()
    out = "".join(router.stream("sys", _msgs(), status=st))
    assert "全部 provider 均失败" in out or "模型流式失败" in out
    assert st.state == DEGRADED
    assert "已完成处理" not in out


# ---------- call_tools 四态 ----------

def test_call_tools_unsupported_is_not_failure(router):
    router.supports_tools = lambda: False
    st = CallStatus()
    res = router.call_tools("sys", _msgs(), [], status=st)
    assert res["supported"] is False
    assert st.state == UNSUPPORTED


def test_call_tools_exception_keeps_supported_true(router):
    """调用异常:supported=True + DEGRADED —— 不回退关键词、保留失败语义。"""
    router._call_openai_tools = lambda *a: (_ for _ in ()).throw(RuntimeError("api down"))
    st = CallStatus()
    res = router.call_tools("sys", _msgs(), [], status=st)
    assert res["supported"] is True and "api down" in res["error"]
    assert st.state == DEGRADED


# ---------- 收集器 ----------

def test_collector_aggregation_and_contextvar():
    c, tok = bind_collector()
    try:
        assert current_collector() is c
        a = c.new()
        a.finish(REAL)
        b = c.new()
        b.finish(FALLBACK)
        assert c.all_real() is False
        assert c.worst_state() == FALLBACK
        assert not c.has_pending()
        d = c.new()
        assert c.has_pending() and c.worst_state() == PENDING
        d.finish(DEGRADED)
        assert c.worst_state() == DEGRADED
    finally:
        reset_collector(tok)
    assert current_collector() is None


def test_collector_isolated_between_tasks():
    c1, t1 = bind_collector()
    a = c1.new()
    reset_collector(t1)
    c2, t2 = bind_collector()
    b = c2.new()
    a.finish(REAL)
    b.finish(REAL)
    reset_collector(t2)
    assert len(c1.snapshot()) == 1 and len(c2.snapshot()) == 1
    assert c1.snapshot()[0] is not c2.snapshot()[0]


if __name__ == "__main__":
    import pytest as _pytest
    raise SystemExit(_pytest.main([__file__, "-q"]))
