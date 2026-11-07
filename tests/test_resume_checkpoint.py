"""断点续跑(P1):步骤级 checkpoint + 续跑复用已完成步骤。

锁住四件事:
  ① `RestartRecovery.record_step/steps_of` 能按 task 回放步骤,`compact_log` 不会把断点压实掉;
  ② 工具步指纹稳定(同名同参 = 同一步,参数字典序无关),失败/需确认的回注文本不入账;
  ③ `_run_tool_calls` 命中续跑缓存时**不再执行**该步,只执行未完成的步并各自记账;
  ④ 生命周期钩子下移基类后,任何装着 `_recovery` 的 Agent 都会落 start/finish(领域 Agent 不再漏记)。
"""

from __future__ import annotations

import types

import pytest

from foxclaw.agents.base import CognitiveAgent
from foxclaw.agents.mixins import _context, tool_exec
from foxclaw.core.recovery import RestartRecovery


class _Rec:
    """鸭子类型替身:只记调用,不做 IO。"""

    def __init__(self, rows=None):
        self.steps: list = []
        self.started: list = []
        self.finished: list = []
        self._rows = rows or []
        self.compacted = False

    def record_step(self, tid, step):
        self.steps.append((tid, step))

    def steps_of(self, tid, limit=200):
        return list(self._rows)

    def task_started(self, **kw):
        self.started.append(kw)

    def task_finished(self, tid, status="done"):
        self.finished.append((tid, status))


# ── ① 日志层 ───────────────────────────────────────────────

def test_record_and_replay_steps(tmp_path) -> None:
    rec = RestartRecovery(str(tmp_path / "recovery.jsonl"), fsync=False)
    rec.record_step("t1", {"kind": "stage", "name": "perceive"})
    rec.record_step("t2", {"kind": "stage", "name": "recall"})
    rec.record_step("t1", {"kind": "tool", "name": "web_search", "key": "k", "result": "r"})
    steps = rec.steps_of("t1")
    assert [s["name"] for s in steps] == ["perceive", "web_search"]
    assert all("event" not in s and "task_id" not in s for s in steps)
    assert rec.steps_of("nope") == []


def test_compact_log_keeps_steps(tmp_path) -> None:
    rec = RestartRecovery(str(tmp_path / "recovery.jsonl"), fsync=False)
    rec.task_started("t1", "任务一")
    rec.record_step("t1", {"kind": "tool", "name": "web_search", "key": "k", "result": "r"})
    rec.task_finished("t1", status="done")
    rec.compact_log(keep_recent_days=7)      # 保留 → 步骤必须还在
    assert len(rec.steps_of("t1")) == 1
    rec.compact_log(keep_recent_days=0)      # 过期 → 记录与步骤一起丢
    assert rec.steps_of("t1") == []


# ── ② 指纹与成败判定 ────────────────────────────────────────

def test_step_key_is_argument_order_insensitive() -> None:
    a = tool_exec._step_key("web_search", {"q": "x", "n": 3})
    b = tool_exec._step_key("web_search", {"n": 3, "q": "x"})
    assert a == b
    assert a != tool_exec._step_key("web_search", {"q": "y", "n": 3})
    assert a != tool_exec._step_key("recall_memory", {"q": "x", "n": 3})


@pytest.mark.parametrize("text,ok", [
    ("【联网搜索结果】...", True),
    ("【召回的相关记忆】...", True),
    ("(工具 web_search 未返回:45s 超时)", False),
    ("(该步需要你确认后才能执行)", False),
    ("(未知工具 foo)", False),
    ("", False),
])
def test_step_ok_rejects_failure_texts(text, ok) -> None:
    assert tool_exec._step_ok(text) is ok


# ── ③ 续跑复用 ──────────────────────────────────────────────

def test_run_tool_calls_reuses_completed_steps(monkeypatch) -> None:
    executed: list = []

    def _fake_batch(agent, calls):
        executed.extend(calls)
        return [f"新结果:{c['name']}" for c in calls]

    monkeypatch.setattr(tool_exec, "_run_tools_batch", _fake_batch)
    rec = _Rec()
    agent = types.SimpleNamespace(_recovery=rec, _session_id="t1")
    done = {"name": "web_search", "args": {"q": "天气"}}
    todo = {"name": "recall_memory", "args": {"q": "偏好"}}
    cache = {tool_exec._step_key("web_search", {"q": "天气"}):
             {"name": "web_search", "result": "【联网搜索结果】晴"}}
    tok = _context._resume_steps.set(cache)
    try:
        out = tool_exec._run_tool_calls(agent, [done, todo])
    finally:
        _context._resume_steps.reset(tok)

    assert executed == [todo], "已完成的步必须复用结果、绝不重复执行"
    assert "断点续跑" in out[0] and "晴" in out[0]
    assert out[1] == "新结果:recall_memory"
    assert [s[1]["key"] for s in rec.steps] == [tool_exec._step_key("recall_memory", {"q": "偏好"})]


def test_run_tool_calls_skips_recording_failures(monkeypatch) -> None:
    monkeypatch.setattr(tool_exec, "_run_tools_batch",
                        lambda agent, calls: ["(工具 web_search 未返回:45s 超时)"])
    rec = _Rec()
    agent = types.SimpleNamespace(_recovery=rec, _session_id="t1")
    tool_exec._run_tool_calls(agent, [{"name": "web_search", "args": {}}])
    assert rec.steps == [], "失败/超时步不能进 checkpoint(否则续跑会把失败当完成)"


# ── ④ 装载与生命周期钩子 ────────────────────────────────────

def test_load_resume_cache_filters_bad_steps() -> None:
    rows = [
        {"kind": "stage", "name": "act"},                                  # 非工具步 → 跳过
        {"kind": "tool", "name": "web_search", "key": "k1", "result": "ok"},
        {"kind": "tool", "name": "web_search", "key": "k2", "result": "x",
         "truncated": True},                                               # 截断 → 不复用
        {"kind": "tool", "name": "web_search", "key": "k3", "result": "  "},  # 空 → 不复用
        {"kind": "tool", "name": "web_search", "result": "no-key"},         # 无指纹 → 跳过
    ]
    fake = types.SimpleNamespace(_recovery=_Rec(rows))
    assert CognitiveAgent._load_resume_cache(fake, "t1") == {"k1": {"name": "web_search",
                                                                  "result": "ok"}}
    assert CognitiveAgent._load_resume_cache(fake, "") == {}
    assert CognitiveAgent._load_resume_cache(types.SimpleNamespace(), "t1") == {}


def test_lifecycle_hooks_record_for_any_agent() -> None:
    rec = _Rec()
    agent = types.SimpleNamespace(_recovery=rec)
    CognitiveAgent._on_session_started(agent, "t1", "跑个任务", {"channel": "cli"})
    CognitiveAgent._on_task_settled(agent, "t1", "done")
    assert rec.started and rec.started[0]["task_id"] == "t1"
    assert rec.started[0]["channel"] == "cli"
    assert rec.finished == [("t1", "done")]

    CognitiveAgent._on_session_started(types.SimpleNamespace(), "t1", "x", {})   # 无 recovery
    CognitiveAgent._on_task_settled(types.SimpleNamespace(), "t1", "done")
    CognitiveAgent._record_stage(types.SimpleNamespace(), "t1", "recall")
