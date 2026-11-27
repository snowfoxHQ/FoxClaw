"""三通道选择机制(G4)—— Runtime 侧确定性决策 + UIA 批量执行。

锁住(基准 v1.0 §L2 + 实施裁定):
  1. 声明只是计划,不是执行许可:授权(deny)先行且独立,通道守卫不改变授权语义;
  2. 降级必须有探测证据:没跑过探测就跳 Visual,一律拒绝;探测失败过 → 放行降级;
  3. 通道切换不产生授权豁免:守卫在授权之后,ui:*/file:* 等不参与 Visual 门控;
  4. 无声明 → 不干预(声明制渐进引入,行为与改前一致);
  5. UIA 批量:整批一次授权/确认(参数指纹绑全量 ops),批内无豁免,逐条回报。
"""
import asyncio
import json
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_EMBEDDING_MODEL"] = "foxclaw-nonexistent-fast"
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.core import channel_policy as cp
from foxclaw.core.task_context import TaskContext
from foxclaw.skills.desktop import desktop_act


# ---------- 1. 动作 → 通道映射 ----------

def test_channel_of_action():
    assert cp.channel_of_action("ui:click") == cp.SEMANTIC
    assert cp.channel_of_action("mouse:click") == cp.VISUAL
    assert cp.channel_of_action("keyboard:type") == cp.VISUAL
    assert cp.channel_of_action("file:write") == cp.PROGRAMMATIC
    assert cp.channel_of_action("shell:run") == cp.PROGRAMMATIC
    assert cp.channel_of_action("未知动作") == ""  # 未知不参与通道门控


# ---------- 2. resolve:确定性通道决策 ----------

def test_resolve_preferred_with_probe_pass():
    r = cp.resolve("semantic", "visual", [{"channel": "semantic", "ok": True}])
    assert r["channel"] == "semantic" and r["degraded"] is False


def test_resolve_probe_required_before_decision():
    """首选未探测 → 不猜:要求先探测,决策挂起。"""
    r = cp.resolve("semantic", "visual", [])
    assert r["channel"] is None and r["reason"].startswith("probe_required:semantic")


def test_resolve_degrade_requires_executed_failure():
    """降级合法路径:首选探测**执行过且失败** → 降级 Visual,带证据。"""
    r = cp.resolve("semantic", "visual",
                   [{"channel": "semantic", "ok": False, "detail": "未找到匹配元素"}])
    assert r["channel"] == "visual" and r["degraded"] is True
    assert r["evidence"][0]["ok"] is False


def test_resolve_fallback_probe_required():
    r = cp.resolve("semantic", "programmatic",
                   [{"channel": "semantic", "ok": False}])
    assert r["channel"] is None and r["reason"].startswith("probe_required:programmatic")


def test_resolve_no_fallback_declared_blocked():
    r = cp.resolve("semantic", "", [{"channel": "semantic", "ok": False}])
    assert r["channel"] is None and "无可用通道" in r["reason"]


def test_resolve_visual_needs_no_probe():
    r = cp.resolve("visual", "", [])
    assert r["channel"] == "visual" and r["degraded"] is False


def test_resolve_invalid_preferred_falls_to_programmatic():
    r = cp.resolve("猜的通道", "visual", [{"channel": "programmatic", "ok": True}])
    assert r["channel"] == "programmatic"


def test_resolve_authorization_is_never_an_input():
    """探测证据里只有能力结果;没探测过 → 先探测,而不是降级(授权缺失不是降级理由)。"""
    r = cp.resolve("semantic", "visual", [{"channel": "semantic", "ok": False}])
    assert r["channel"] == "visual"  # 只有探测失败才降级
    r2 = cp.resolve("semantic", "visual", [])
    assert r2["reason"].startswith("probe_required")


# ---------- 3. visual_gate:执行前校验 ----------

def test_gate_no_declaration_noop():
    assert cp.visual_gate(None, [], "mouse:click") == ""
    assert cp.visual_gate({}, [], "mouse:click") == ""


def test_gate_visual_preferred_allowed():
    assert cp.visual_gate({"preferred": "visual", "fallback": ""}, [], "mouse:click") == ""


def test_gate_blocks_visual_without_probe_evidence():
    plan = {"preferred": "semantic", "fallback": "visual"}
    assert "不得凭猜测直接 Visual" in cp.visual_gate(plan, [], "mouse:click")


def test_gate_allows_visual_after_failed_probe():
    plan = {"preferred": "semantic", "fallback": "visual"}
    probes = [{"channel": "semantic", "ok": False, "detail": "未找到匹配元素"}]
    assert cp.visual_gate(plan, probes, "mouse:click") == ""


def test_gate_only_gates_visual_actions():
    plan = {"preferred": "semantic", "fallback": "visual"}
    assert cp.visual_gate(plan, [], "ui:invoke") == ""   # ui:* 不受 Visual 门控
    assert cp.visual_gate(plan, [], "file:write") == ""  # 程序化不受 Visual 门控


# ---------- 4. desktop_act 边界:授权先行,守卫在后 ----------

class _FakeVerdict:
    def __init__(self, level):
        self.level = level
        self.reason = "test policy"
        self.decision_id = "decision-1"


class _FakeGate:
    def __init__(self, level="allow"):
        self.level = level

    def check_action(self, spec, args, rules=None, confirmed=False):
        return _FakeVerdict(self.level)


def _run(gate, act_impl, action, args):
    async def act(action_, a):
        return await act_impl(a)
    return asyncio.run(desktop_act(gate, act, action, args, confirmed=True, session="s"))


def test_desktop_act_channel_guard_blocks_unprobed_visual():
    from foxclaw.agents.mixins._context import _active_tc
    tc = TaskContext.create("s1", "任务", [], channel_plan={"preferred": "semantic",
                                                            "fallback": "visual"})
    tok = _active_tc.set(tc)
    try:
        async def act(a):
            raise AssertionError("守卫拒绝时不应执行 Visual 动作")
        r = _run(_FakeGate("allow"), act, "mouse:click", {"x": 1, "y": 2})
        assert r["ok"] is False and r.get("channel_guard") is True
        assert "不得凭猜测直接 Visual" in r["reason"]
    finally:
        _active_tc.reset(tok)


def test_desktop_act_channel_guard_allows_after_probe_failure():
    from foxclaw.agents.mixins._context import _active_tc
    tc = TaskContext.create("s1", "任务", [], channel_plan={"preferred": "semantic",
                                                            "fallback": "visual"})
    tc.add_probe("semantic", False, "未找到匹配元素")  # 探测执行过且失败
    tok = _active_tc.set(tc)
    try:
        async def act(a):
            return {"ok": True, "clicked": True}
        r = _run(_FakeGate("allow"), act, "mouse:click", {"x": 1, "y": 2})
        assert r["ok"] is True and r["execution_backend"] == "local"
    finally:
        _active_tc.reset(tok)


def test_desktop_act_no_declaration_unchanged():
    """无声明(HTTP 直连/未声明任务)→ 守卫不干预,行为与改前一致。"""
    async def act(a):
        return {"ok": True}
    assert _run(_FakeGate("allow"), act, "mouse:click", {"x": 1, "y": 2})["ok"] is True


def test_desktop_act_deny_precedes_channel_guard():
    """授权先行:deny 与通道守卫无关,授权语义不被通道机制改变。"""
    from foxclaw.agents.mixins._context import _active_tc
    tc = TaskContext.create("s1", "任务", [], channel_plan={"preferred": "visual"})
    tok = _active_tc.set(tc)
    try:
        async def act(a):
            raise AssertionError("deny 不应执行")
        r = _run(_FakeGate("deny"), act, "mouse:click", {"x": 1, "y": 2})
        assert r["denied"] is True and r["level"] == "deny"
        assert "channel_guard" not in r
    finally:
        _active_tc.reset(tok)


def test_desktop_act_ui_action_not_gated():
    """声明 semantic 首选时,ui:* 本身就是首选通道动作,不受 Visual 门控。"""
    from foxclaw.agents.mixins._context import _active_tc
    tc = TaskContext.create("s1", "任务", [], channel_plan={"preferred": "semantic"})
    tok = _active_tc.set(tc)
    try:
        async def act(a):
            return {"ok": True}
        r = _run(_FakeGate("allow"), act, "ui:invoke", {"locator": {"index": 3}})
        assert r["ok"] is True
    finally:
        _active_tc.reset(tok)


# ---------- 5. 探测证据落账(授权结果绝不进证据) ----------

def test_probe_recording_in_invoke_desktop_act(monkeypatch):
    from foxclaw.agents.mixins import tool_exec as te
    from foxclaw.agents.mixins._context import _active_tc
    tc = TaskContext.create("s-probe", "任务", [], channel_plan={"preferred": "semantic"})
    tok = _active_tc.set(tc)
    try:
        async def probe_fail_cap(action, args, confirmed=False):
            return {"ok": False, "error": "未找到匹配元素"}  # 探测失败(非授权拒绝)

        agent = types.SimpleNamespace(_caps={"desktop_act": probe_fail_cap})
        te._invoke_desktop_act(agent, {"action": "ui:locate",
                                       "args": {"locator": {"index": 1}}}, lambda t: None)
        probes = tc.to_dict()["channel_probes"]
        assert len(probes) == 1 and probes[0]["channel"] == "semantic"
        assert probes[0]["ok"] is False and "未找到匹配元素" in probes[0]["detail"]

        # 授权拒绝不是探测:denied 结果不落账
        async def denied_cap(action, args, confirmed=False):
            return {"ok": False, "denied": True, "level": "deny", "reason": "策略"}

        agent2 = types.SimpleNamespace(_caps={"desktop_act": denied_cap})
        te._invoke_desktop_act(agent2, {"action": "ui:locate",
                                        "args": {"locator": {"index": 1}}}, lambda t: None)
        assert len(tc.to_dict()["channel_probes"]) == 1
    finally:
        _active_tc.reset(tok)


def test_channel_policy_record_probe_bounded_and_safe():
    tc = TaskContext.create("t", "任务", [])
    for i in range(30):
        cp.record_probe(tc, "semantic", i % 2 == 0, f"p{i}")
    assert len(tc.to_dict()["channel_probes"]) <= cp.PROBE_LIMIT
    cp.record_probe(tc, "伪造通道", True)   # 非法通道拒绝
    cp.record_probe(None, "semantic", True)  # None 安全
    assert all(p["channel"] == "semantic" for p in tc.to_dict()["channel_probes"])


# ---------- 6. UIA 批量(离线单测:stub powershell 层) ----------

def test_ui_batch_sync_builds_and_parses(monkeypatch):
    from foxclaw.desktop import uia
    captured = {}

    def fake_run(script, env_extra, timeout=20):
        captured["env"] = env_extra
        return {"ok": True, "count": 2,
                "results": [{"ok": True, "action": "invoke", "name": "确定"},
                            {"ok": False, "action": "set_value", "error": "不支持"}]}

    monkeypatch.setattr(uia, "_run_powershell", fake_run)
    r = uia.ui_batch_sync([
        {"action": "invoke", "locator": {"index": 3}},
        {"action": "set_value", "locator": {"automation_id": "kw"}, "value": "查询"},
        {"action": "mouse:click"},           # 非法动作被剔除
        "not-a-dict",                        # 非法条目被剔除
    ])
    assert r["ok"] is True and r["count"] == 2
    ops = json.loads(captured["env"]["FOXCLAW_UIA_OPS"])
    assert [o["action"] for o in ops] == ["invoke", "set_value"]
    assert ops[1]["value"] == "查询" and ops[1]["locator"] == {"automation_id": "kw"}


def test_ui_batch_sync_validates_input(monkeypatch):
    from foxclaw.desktop import uia
    monkeypatch.setattr(uia, "_run_powershell", lambda *a, **k: {"ok": True})
    assert uia.ui_batch_sync("nope")["ok"] is False
    assert uia.ui_batch_sync([{"action": "未知"}])["ok"] is False
    assert uia.ui_batch_sync([])["ok"] is False


# ---------- 7. TaskContext:通道声明不可放宽 ----------

def test_task_context_channel_plan_immutable_and_persisted():
    tc = TaskContext.create("t", "任务", [], channel_plan={"preferred": "semantic",
                                                           "fallback": "visual"})
    assert tc.channel_plan == {"preferred": "semantic", "fallback": "visual"}
    try:  # 无 setter(与 constraints 同为不可放宽项)
        tc.channel_plan = {"preferred": "visual"}
        raise AssertionError("channel_plan 不应可赋值")
    except AttributeError:
        pass
    d = tc.to_dict()  # 快照 roundtrip 与续跑继承
    r = TaskContext.from_resumed(d, "t2")
    assert r.channel_plan == {"preferred": "semantic", "fallback": "visual"}
    bad = TaskContext.create("t3", "任务", [], channel_plan={"preferred": "瞎写"})
    assert bad.channel_plan == {}  # 非法声明被规范化拒绝
