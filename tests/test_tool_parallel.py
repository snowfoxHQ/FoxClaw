"""工具调度:只读并发 / 写与未声明串行 / 单工具超时 / 污点回主线程合并。

背景:`agents/mixins/tool_exec.py` 原本逐个 `_dispatch_tool` 串行,一个慢工具阻塞整轮,
且完全无单工具超时——模型一次发两个只读工具(联网+召回)要等两倍时间,网络卡死则整轮挂住。
"""

from __future__ import annotations

import threading
import time

from foxclaw.agents.mixins import tool_exec as _te
from foxclaw.agents.mixins.tool_exec import ToolExecMixin


class _Fake(ToolExecMixin):
    """只保留调度层:闸门走真实 registry + permission,执行被 monkeypatch 替换。"""

    def __init__(self):
        self.calls: list[str] = []
        self._taint_level = 0

    def _think(self, **kwargs):
        return None, None


def _make(monkeypatch, behavior) -> _Fake:
    fake = _Fake()

    def _invoke(agent, name, args, taint_sink=None):
        agent.calls.append(name)
        return behavior(name, args, taint_sink)

    monkeypatch.setattr(_te, "_invoke_tool", _invoke)
    return fake


def _calls(*pairs):
    return [{"name": n, "args": a} for n, a in pairs]


def test_readonly_tools_run_concurrently(monkeypatch) -> None:
    barrier = threading.Barrier(2, timeout=3)

    def behave(name, args, sink):
        barrier.wait()          # 串行时第二个永远到不了 → BrokenBarrierError
        return f"{name}:ok"

    fake = _make(monkeypatch, behave)
    t0 = time.monotonic()
    out = fake._run_tools(_calls(("web_search", {"query": "python 3.14"}),
                                 ("recall_memory", {"query": "项目约定"})))
    elapsed = time.monotonic() - t0
    assert out == ["web_search:ok", "recall_memory:ok"]
    assert elapsed < 2.0, f"两个只读工具应并发执行,实际耗时 {elapsed:.2f}s"


def test_slow_tool_is_cut_off(monkeypatch) -> None:
    monkeypatch.setenv("FOXCLAW_TOOL_TIMEOUT", "0.3")

    def behave(name, args, sink):
        time.sleep(2)
        return "late"

    fake = _make(monkeypatch, behave)
    t0 = time.monotonic()
    out = fake._dispatch_tool("web_search", {"query": "x"})
    assert "超过" in out and "未返回" in out
    assert "不要编造" in out
    assert time.monotonic() - t0 < 1.5, "超时后必须立刻返回,不能等工具自己结束"


def test_taint_collected_in_worker_and_merged_in_main(monkeypatch) -> None:
    def behave(name, args, sink):
        sink.append(3 if name == "web_search" else 2)   # worker 只能收集,不能直接写会话污点
        return "ok"

    fake = _make(monkeypatch, behave)
    fake._run_tools(_calls(("web_search", {"query": "x"}),
                           ("recall_memory", {"query": "y"})))
    assert fake._taint_level == 3, "并发执行后污点应取最大值合并回会话"


def test_single_readonly_call_still_merged(monkeypatch) -> None:
    def behave(name, args, sink):
        sink.append(2)
        return "ok"

    fake = _make(monkeypatch, behave)
    assert fake._dispatch_tool("recall_memory", {"query": "y"}) == "ok"
    assert fake._taint_level == 2


def test_undeclared_tool_never_executed_and_batch_serialized(monkeypatch) -> None:
    def behave(name, args, sink):
        return "ok"

    fake = _make(monkeypatch, behave)
    out = fake._run_tools(_calls(("web_search", {"query": "x"}),
                                 ("write_file", {"path": "a.md"})))
    assert "未知工具 write_file" in out[1]
    assert fake.calls == ["web_search"], "未声明能力的工具必须被闸门挡住,不进入执行"


def test_missing_required_arg_reports_instead_of_executing(monkeypatch) -> None:
    def behave(name, args, sink):                 # pragma: no cover - 不应被调用
        raise AssertionError("入参校验失败时不该执行工具")

    fake = _make(monkeypatch, behave)
    out = fake._run_tools([{"name": "web_search", "args": {}}])
    assert "入参不合法" in out[0]
    assert "query" in out[0]
    assert fake.calls == []
