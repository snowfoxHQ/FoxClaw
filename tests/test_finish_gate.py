"""Finish Gate(G3)—— 任务终局的确定性核验,三态诚实,不消费模型自我报告。

锁住(基准 v1.0 §L4 + G3 验收):
  1. verify:file 写/删不再一律 UNVERIFIED——磁盘就是后置条件(存在/非空/已删);
  2. gate:PASS / FAIL / UNKNOWN 三态;UNKNOWN 绝不当 PASS(结构检查做不了就如实说);
  3. 终局门控:模型宣布完成但产物结构不符 → 拒绝 COMPLETED,降级并回注结构化原因;
  4. 无可检查对象(纯对话任务)→ UNKNOWN,不降级(诚实 ≠ 失败);
  5. _settle 记账判定结构化:失败策略结论 ≠ OK 的步骤不进 checkpoint。
"""
import asyncio
import sys
import types
from pathlib import Path

os_env = None  # noqa: 占位,env 设置见下
import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_EMBEDDING_MODEL"] = "foxclaw-nonexistent-fast"
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.core import verify as vf
from foxclaw.core import failure_policy as fp
from foxclaw.core.finish_gate import evaluate, reject_message, PASS, FAIL, UNKNOWN
from foxclaw.core.task_context import TaskContext


# ---------- 1. verify:file 写/删的确定性后置条件 ----------

def _dact(action, **inner):
    return {"action": action, "args": inner}


def test_file_write_verified_when_on_disk(tmp_path):
    p = tmp_path / "out.txt"
    p.write_text("内容", encoding="utf-8")
    v = vf.verify("desktop_act", _dact("file:write", path=str(p), content="内容"),
                  f'{{"ok": true, "path": "{p}"}}', {"read_only": False})
    assert v.status == vf.VERIFIED, v.reason


def test_file_write_unverified_when_missing(tmp_path):
    p = tmp_path / "gone.txt"
    v = vf.verify("desktop_act", _dact("file:write", path=str(p), content="x"),
                  '{"ok": true}', {"read_only": False})
    # 诚实:工具说 ok 但磁盘没有 → 未核验(不是失败也不是成功)
    assert v.status == vf.UNVERIFIED and "未落盘" in v.reason


def test_file_write_unverified_when_empty_file(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_text("", encoding="utf-8")
    v = vf.verify("desktop_act", _dact("file:write", path=str(p), content=""),
                  '{"ok": true}', {"read_only": False})
    assert v.status == vf.UNVERIFIED and "空文件" in v.reason


def test_file_delete_verified_when_gone(tmp_path):
    p = tmp_path / "del.txt"  # 从不创建 = 已不存在
    v = vf.verify("desktop_act", _dact("file:delete", path=str(p)),
                  '{"ok": true}', {"read_only": False})
    assert v.status == vf.VERIFIED


def test_file_delete_unverified_when_still_there(tmp_path):
    p = tmp_path / "still.txt"
    p.write_text("x", encoding="utf-8")
    v = vf.verify("desktop_act", _dact("file:delete", path=str(p)),
                  '{"ok": true}', {"read_only": False})
    assert v.status == vf.UNVERIFIED and "仍然存在" in v.reason


def test_shell_run_stays_unverified():
    """无观察后置条件的写动作保持诚实 UNVERIFIED(不因接入 file 检查而放宽)。"""
    v = vf.verify("desktop_act", _dact("shell:run", command="echo hi"),
                  '{"ok": true, "returncode": 0}', {"read_only": False})
    assert v.status == vf.UNVERIFIED


# ---------- 2. gate:三态评估 ----------

def test_gate_unknown_when_nothing_checkable():
    tc = TaskContext.create("t1", "纯对话任务", [])
    r = evaluate(tc)
    assert r["status"] == UNKNOWN and r["checked"] == 0 and r["failures"] == []


def test_gate_pass_when_artifacts_intact(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("x", encoding="utf-8")
    tc = TaskContext.create("t1", "写文件", [])
    tc.add_artifact("file", str(p))
    r = evaluate(tc)
    assert r["status"] == PASS and r["checked"] == 1


def test_gate_fail_when_artifact_missing(tmp_path):
    tc = TaskContext.create("t1", "写文件", [])
    tc.add_artifact("file", str(tmp_path / "vanished.txt"))
    r = evaluate(tc)
    assert r["status"] == FAIL
    assert r["failures"][0]["ref"].endswith("vanished.txt")
    assert "不存在" in reject_message(r)


def test_gate_fail_when_declared_deleted_still_exists(tmp_path):
    p = tmp_path / "keep.txt"
    p.write_text("x", encoding="utf-8")
    tc = TaskContext.create("t1", "删文件", [])
    tc.add_artifact("file_deleted", str(p))
    assert evaluate(tc)["status"] == FAIL
    p.unlink()
    assert evaluate(tc)["status"] == PASS


def test_gate_never_raises_on_garbage():
    r = evaluate(None)
    assert r["status"] == UNKNOWN


# ---------- 3. 终局门控:拒绝假完成 / UNKNOWN 不降级 ----------

def _ns_agent(pipe, settled):
    """结算测试同款假 Agent(_run_task_core 真身 + 假管线)。"""
    from foxclaw.core.task_context import TaskContext as _TC
    ns = types.SimpleNamespace()
    ns.AGENT_ID = "base"
    ns.start_session = lambda goal, entities: "sess-g1"
    ns._pinned_session = lambda sid, ctx: sid
    ns._task_entities = lambda task: []
    ns._on_session_started = lambda *a, **k: None
    ns._prefs_context = lambda: ""
    ns._cognitive_pipeline = lambda: pipe
    ns._record_stage = lambda *a: None
    ns._emit_execution = lambda *a, **k: None
    ns._maybe_store_preference = lambda t: None
    ns._task_context_start = lambda sid, task, cons, resume_tid="": _TC.create(
        sid, task, cons)
    ns._save_task_state = lambda sid, tc: None
    ns._on_task_settled = lambda sid, status: settled.append((sid, status))
    ns.thought_chain = types.SimpleNamespace(get_summary=lambda aid: "")
    ns.router = types.SimpleNamespace()
    return ns


class _Pipe:
    def __init__(self, result="完成", artifact=None):
        self.result = result
        self.artifact = artifact  # (kind, ref) → 经 _active_tc 落产物

    async def run(self, ctx, on_stage=None):
        from foxclaw.agents.mixins._context import _active_tc
        if self.artifact is not None:
            tc = _active_tc.get()
            assert tc is not None, "任务执行期间 _active_tc 必须已挂载"
            tc.add_artifact(*self.artifact)
        ctx.result = self.result


def _run(agent):
    from foxclaw.agents.base import CognitiveAgent
    return asyncio.run(CognitiveAgent._run_task_core(agent, "任务"))


def test_gate_rejects_fake_completion(tmp_path):
    """模型宣布完成,但产物文件已被删 → 拒绝 COMPLETED,降级 FAILED + 结构化原因。"""
    p = tmp_path / "gone.txt"  # 记账过但现已不存在
    settled: list = []
    agent = _ns_agent(_Pipe(artifact=("file", str(p))), settled)
    resp = _run(agent)
    assert resp["task_status"] == "FAILED"
    assert resp["finish_gate"]["status"] == FAIL
    assert "不存在" in resp["result"]
    assert settled[-1] == ("sess-g1", "FAILED")
    assert resp["task_context"]["status"] == "FAILED"
    assert resp["task_context"]["last_verified_state"] == "finish_gate=FAIL"


def test_gate_pass_keeps_completion(tmp_path):
    p = tmp_path / "ok.txt"
    p.write_text("x", encoding="utf-8")
    settled: list = []
    agent = _ns_agent(_Pipe(artifact=("file", str(p))), settled)
    resp = _run(agent)
    assert resp["task_status"] == "COMPLETED"
    assert resp["finish_gate"]["status"] == PASS
    assert settled[-1] == ("sess-g1", "COMPLETED")


def test_gate_unknown_does_not_demote_dialog_task():
    """纯对话任务无可检查对象 → UNKNOWN,诚实上报但**不降级**(UNKNOWN ≠ FAIL)。"""
    settled: list = []
    agent = _ns_agent(_Pipe(), settled)
    resp = _run(agent)
    assert resp["task_status"] == "COMPLETED"
    assert resp["finish_gate"]["status"] == UNKNOWN
    assert settled[-1] == ("sess-g1", "COMPLETED")


# ---------- 4. _settle 记账判定结构化 ----------

def test_settle_uses_structured_failure_verdict(monkeypatch, tmp_path):
    """失败策略结论 ≠ OK 的步骤不进 checkpoint——即使文本不带 ( 前缀也不行。"""
    from foxclaw.agents.mixins import tool_exec as te
    p = tmp_path / "t.txt"
    p.write_text("x", encoding="utf-8")
    rec: list = []
    monkeypatch.setattr(te, "_record_step", lambda *a, **k: rec.append(a))
    agent = types.SimpleNamespace(_recovery=None, _session_id="s")
    call = {"name": "desktop_act", "args": _dact("file:read", path=str(p))}
    # 模拟:失败策略判定为 TIMEOUT,但回注文本不带 ( 前缀
    monkeypatch.setattr(te, "_apply_failure_policy",
                        lambda a, c, r: (f"工具超时:{r}", fp.TIMEOUT))
    out = te._settle(agent, call, "工具超时:xx")
    assert rec == [], "TIMEOUT 结论的步骤绝不能进 checkpoint"
    assert "超时" in out
    # kind=OK 时正常记账
    monkeypatch.setattr(te, "_apply_failure_policy",
                        lambda a, c, r: (r, fp.OK))
    te._settle(agent, call, '{"ok": true, "content": "x"}')
    assert len(rec) == 1


def test_settle_records_verified_file_artifact(tmp_path):
    """核验通过的 file:write → 产物引用落 TaskContext(经 _active_tc)。"""
    from foxclaw.agents.mixins import tool_exec as te
    from foxclaw.agents.mixins._context import _active_tc
    p = tmp_path / "artifact.txt"
    p.write_text("内容", encoding="utf-8")
    tc = TaskContext.create("s-art", "写文件", [])
    tok = _active_tc.set(tc)
    try:
        agent = types.SimpleNamespace(_recovery=None, _session_id="s-art")
        call = {"name": "desktop_act",
                "args": _dact("file:write", path=str(p), content="内容")}
        out = te._settle(agent, call, f'{{"ok": true, "path": "{p}", "size": 6}}')
        assert '"ok": true' in out
        arts = tc.to_dict()["artifacts"]
        assert len(arts) == 1 and arts[0]["kind"] == "file"
        assert arts[0]["ref"] == str(p)
        assert evaluate(tc)["status"] == PASS
    finally:
        _active_tc.reset(tok)


def test_settle_does_not_record_unverified_artifact(tmp_path):
    """核验未通过的写步骤绝不落产物(产物只认 VERIFIED)。"""
    from foxclaw.agents.mixins import tool_exec as te
    from foxclaw.agents.mixins._context import _active_tc
    tc = TaskContext.create("s-art2", "写文件", [])
    tok = _active_tc.set(tc)
    try:
        agent = types.SimpleNamespace(_recovery=None, _session_id="s-art2")
        call = {"name": "desktop_act",
                "args": _dact("file:write", path=str(tmp_path / "nope.txt"),
                              content="x")}
        te._settle(agent, call, '{"ok": true, "path": "nope.txt"}')
        assert tc.to_dict()["artifacts"] == []
        assert evaluate(tc)["status"] == UNKNOWN
    finally:
        _active_tc.reset(tok)
