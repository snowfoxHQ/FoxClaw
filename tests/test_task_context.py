"""TaskContext(G2)—— 任务级结构化状态:新建 / 中断 / 重启 / 恢复全程约束不丢失。

锁住(基准 v1.0 §L1 + G2 验收):
  1. constraints / acceptance_criteria 创建后不可放宽;验收条件只能更新验证结果;
  2. run_task(constraints=...) → 快照落盘(task_state 事件),返回体带 task_context;
  3. 断点续跑:约束/验收条件以已落盘快照为权威,重放时传新约束不生效;
  4. 中断 → 重启:scan_interrupted 识别 + task_state_of 恢复约束;
  5. compact_log 保留每个任务最新一份快照(压实不丢状态)。
"""
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_EMBEDDING_MODEL"] = "foxclaw-nonexistent-fast"
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.core.recovery import RestartRecovery
from foxclaw.core.task_context import TaskContext


# ---------- 1. 状态对象:不可放宽 + 只更新验证结果 ----------

def test_create_and_constraints_immutable():
    tc = TaskContext.create("t1", "整理文件", ["保留原始工作表", "输出到指定目录"])
    assert tc.constraints == ("保留原始工作表", "输出到指定目录")
    crit = {c["id"]: c for c in tc.acceptance_criteria}
    assert crit["goal"]["kind"] == "semantic" and crit["goal"]["result"] == "unknown"
    assert crit["c1"]["kind"] == "constraint" and crit["c1"]["result"] == "pending"
    # 返回的 dict 是深拷贝:改外部副本不影响内部状态
    d = tc.to_dict()
    d["constraints"].append("伪造约束")
    assert "伪造约束" not in tc.constraints
    # constraints 无公开 setter(基准:用户约束不可被模型计划覆盖)
    with pytest.raises(AttributeError):
        tc.constraints = ("放宽",)
    assert tc.constraints == ("保留原始工作表", "输出到指定目录")


def test_criterion_only_result_can_change():
    tc = TaskContext.create("t1", "目标", ["约束A"])
    assert tc.update_criterion_result("c1", "pass") is True
    assert tc.acceptance_criteria[1]["result"] == "pass"
    # 未知 id / 非法结果 → 拒绝
    assert tc.update_criterion_result("nope", "pass") is False
    assert tc.update_criterion_result("c1", "放宽") is False
    # 文本/类别不可经 update 改动(不得中途放宽)
    assert tc.acceptance_criteria[1]["text"] == "约束A"
    assert tc.acceptance_criteria[1]["kind"] == "constraint"


def test_roundtrip_and_resume_snapshot_authoritative():
    tc = TaskContext.create("t1", "目标", ["约束A"])
    tc.add_fact("文件已读取", source="file:read")
    tc.add_artifact("file", "D:/out/result.xlsx")
    tc.record_conflict("两份清单不一致", evidence="file:read + ui:extract")
    d = tc.to_dict()
    tc2 = TaskContext(d)
    assert tc2.constraints == tc.constraints and tc2.goal == tc.goal
    # 续跑:新 task_id + resumed_from,约束/验收条件/进度继承自快照
    r = TaskContext.from_resumed(d, "t2")
    assert r.task_id == "t2" and r.resumed_from == "t1"
    assert r.constraints == ("约束A",) and r.status == "in_progress"
    assert r.acceptance_criteria == tc.acceptance_criteria
    assert [f["fact"] for f in r.to_dict()["discovered_facts"]] == ["文件已读取"]
    assert r.to_dict()["artifacts"][0]["ref"] == "D:/out/result.xlsx"
    assert len(r.to_dict()["unresolved_conflicts"]) == 1
    # 损坏快照 → 拒绝恢复,交由上层按新建处理
    with pytest.raises(ValueError):
        TaskContext.from_resumed({}, "t3")


# ---------- 2. JSONL 通道:追加 / 取最新 / 容错 / 压实保留 ----------

def _recovery(tmpdir, boot_id="b1") -> RestartRecovery:
    return RestartRecovery(str(Path(tmpdir) / "task_log.jsonl"),
                           boot_id=boot_id, fsync=False)


def test_record_and_latest_state(tmp_path):
    rec = _recovery(tmp_path)
    rec.record_task_state("t1", {"status": "in_progress", "constraints": ["A"]})
    rec.record_task_state("t1", {"status": "in_progress", "constraints": ["A", "B"]})
    assert rec.task_state_of("t1")["constraints"] == ["A", "B"]  # 最新一份
    assert rec.task_state_of("missing") == {}
    # 损坏行容错:跳过,不影响已读到的最新快照
    with open(rec.log_path, "a", encoding="utf-8") as f:
        f.write("{broken json\n")
    assert rec.task_state_of("t1")["constraints"] == ["A", "B"]


def test_compact_preserves_latest_snapshot(tmp_path):
    rec = _recovery(tmp_path)
    rec.task_started("t1", "任务")
    rec.record_task_state("t1", {"constraints": ["A"], "status": "in_progress"})
    rec.record_task_state("t1", {"constraints": ["A", "B"], "status": "in_progress"})
    rec.task_finished("t1")
    rec.compact_log(keep_recent_days=7)
    # 压实后:每任务只留最新一份快照,且不丢
    assert rec.task_state_of("t1")["constraints"] == ["A", "B"]


# ---------- 3. 任务级验收:run_task 全程约束不丢失 ----------

def test_run_task_constraints_survive_create_and_resume():
    d = tempfile.mkdtemp(prefix="taskctx_run_")
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        cons = ["保留原始工作表", "结果存到指定目录"]
        r = asyncio.run(os_.finance.run_task("整理三个文件的数据", constraints=cons))
        tc = r["task_context"]
        assert tc["constraints"] == cons                      # 新建:约束进状态对象
        assert tc["status"] == r["task_status"]               # 终态快照随结算落盘
        state = os_.finance._recovery.task_state_of(r["session_id"])
        assert state.get("constraints") == cons               # JSONL 通道可查回
        # 断点续跑:重放时传「放宽」的约束 → 已落盘快照权威,原约束原样保留
        r2 = asyncio.run(os_.finance.run_task(
            "整理三个文件的数据", context={"_resume_task_id": r["session_id"]},
            constraints=["随便处理"]))
        tc2 = r2["task_context"]
        assert tc2["constraints"] == cons
        assert tc2["resumed_from"] == r["session_id"]
        assert any(c["id"] == "c1" and c["text"] == "保留原始工作表"
                   for c in tc2["acceptance_criteria"])
        asyncio.run(os_.aclose())
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_interrupted_state_recoverable_after_restart():
    d = tempfile.mkdtemp(prefix="taskctx_crash_")
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        rec = os_.finance._recovery
        log_path = rec.log_path
        cons = ["不删除原始文件"]
        # 模拟「跑到一半进程没了」:in_progress + 快照已落盘,无 finish 记录
        rec.task_started("t-crash", "崩溃任务")
        tc = TaskContext.create("t-crash", "崩溃任务", cons)
        tc.add_fact("源文件已读取", source="file:read")
        rec.record_task_state("t-crash", tc.to_dict())
        asyncio.run(os_.aclose())
        # 「重启」:新 boot_id 的恢复器重放同一份日志
        rec2 = RestartRecovery(log_path, boot_id="boot-new")
        interrupted = rec2.scan_interrupted()
        assert "t-crash" in [x.task_id for x in interrupted]
        state = rec2.task_state_of("t-crash")
        assert state.get("constraints") == cons               # 中断→重启,约束未丢
        assert [f["fact"] for f in state.get("discovered_facts", [])] == ["源文件已读取"]
        # 恢复对象:快照 → 新 TaskContext,约束/事实原样继承
        restored = TaskContext.from_resumed(state, "t-crash-2")
        assert restored.constraints == tuple(cons)
        assert restored.resumed_from == "t-crash"
    finally:
        shutil.rmtree(d, ignore_errors=True)
