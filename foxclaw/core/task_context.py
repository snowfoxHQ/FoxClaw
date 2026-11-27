"""TaskContext — 任务级结构化状态(基准 v1.0 §L1 Task Runtime)。

三类数据严格分工(与冻结基准一致):
  · TaskContext —— 当前任务的权威状态:goal / constraints / 验收条件 / 进度 / 产物;
  · task_log.jsonl —— 追加式持久化通道(event=task_state 快照,由 recovery 写);
  · PipelineContext —— 单次执行期间的临时上下文。

硬约束(实施依据 G2):
  · constraints 与 acceptance_criteria 在任务开始时生成,创建后**不可被模型放宽**;
    恢复(resume)时以已落盘快照为权威,不接受调用方重放时传入的新约束。
  · 验收条件只能更新验证结果(update_criterion_result),不能改文本/类别。
  · TaskContext 不接管 HMR:管当前任务,不管跨任务记忆。
  · 本模块只是内存对象 + 序列化,不建第二套存储;进度类列表有界,不当内存放大器。
"""
from __future__ import annotations

import copy
import time

SCHEMA_VERSION = 1

STATUS_IN_PROGRESS = "in_progress"
# 终态沿用 P0-03.3 的统一结算状态(COMPLETED/PARTIAL/FAILED/INTERRUPTED/BLOCKED)。
CRITERION_RESULTS = ("pending", "pass", "fail", "unknown")
CRITERION_KINDS = ("constraint", "structural", "semantic")

_LIST_LIMIT = 50
_FACT = 500        # 单条事实/条目文本截断
_GOAL = 2000

_FIELDS = {"task_id", "goal", "constraints", "acceptance_criteria", "current_focus",
           "completed", "pending", "discovered_facts", "unresolved_conflicts",
           "artifacts", "last_verified_state", "status", "schema_version",
           "created_at", "updated_at", "resumed_from",
           "channel_plan", "channel_probes"}


def _clip(text, limit) -> str:
    return str(text or "")[:limit]


def _bounded(items, limit=_LIST_LIMIT) -> list:
    """只保留最近 limit 条:进度列表要有界,长任务不能把状态对象吃成无界内存。"""
    if not isinstance(items, list):
        return []
    return items[-limit:]


class TaskContext:
    """任务状态对象。内部持有一份 dict,所有变更走窄方法;constraints /
    acceptance_criteria 的文本与类别创建后只读。"""

    def __init__(self, data: dict):
        d = dict(data or {})
        self._data = {
            "task_id": _clip(d.get("task_id"), 128),
            "goal": _clip(d.get("goal"), _GOAL),
            "constraints": [s for s in _bounded(
                [str(c)[:_FACT] for c in (d.get("constraints") or [])]) if s],
            "acceptance_criteria": self._norm_criteria(d.get("acceptance_criteria")),
            "current_focus": _clip(d.get("current_focus"), _FACT),
            "completed": _bounded(d.get("completed") or []),
            "pending": _bounded(d.get("pending") or []),
            "discovered_facts": _bounded(d.get("discovered_facts") or []),
            "unresolved_conflicts": _bounded(d.get("unresolved_conflicts") or []),
            "artifacts": _bounded(d.get("artifacts") or []),
            "last_verified_state": _clip(d.get("last_verified_state"), _FACT),
            "status": _clip(d.get("status") or STATUS_IN_PROGRESS, 32),
            "schema_version": SCHEMA_VERSION,
            "created_at": float(d.get("created_at") or time.time()),
            "updated_at": float(d.get("updated_at") or time.time()),
            "resumed_from": _clip(d.get("resumed_from"), 128),
            # 【G4·三通道】任务级通道声明(创建时定,与 constraints 同为不可放宽项)
            # + 能力探测证据(只认探测结果,授权结果绝不进这里,见 channel_policy)。
            "channel_plan": self._norm_plan(d.get("channel_plan")),
            "channel_probes": _bounded(d.get("channel_probes") or []),
        }

    @staticmethod
    def _norm_plan(raw) -> dict:
        if not isinstance(raw, dict):
            return {}
        pref = str(raw.get("preferred") or "")
        fb = str(raw.get("fallback") or "")
        from .channel_policy import CHANNELS
        pref = pref if pref in CHANNELS else ""
        fb = fb if fb in CHANNELS and fb != pref else ""
        return {"preferred": pref, "fallback": fb} if pref else {}

    # ---------- 构造 ----------

    @classmethod
    def create(cls, task_id: str, goal: str, constraints=None,
               channel_plan: dict | None = None) -> "TaskContext":
        """任务开始时创建:验收条件由 goal + constraints 显式生成。
        goal 是语义要求(无法确定性验证 → 初始 unknown,不许假装 pass);
        每条用户约束是约束检查(初始 pending,G3 接确定性检查器)。
        channel_plan(G4):子目标的通道声明 {preferred, fallback},创建时定,
        创建后不可改(与 constraints 同为不可放宽项)。"""
        goal = _clip(goal, _GOAL)
        if isinstance(constraints, str):  # 防御:传字符串别被逐字符拆开
            constraints = [constraints]
        constraints = [s for s in (str(c).strip()[:_FACT] for c in (constraints or [])) if s]
        criteria = [{"id": "goal", "kind": "semantic", "text": goal, "result": "unknown"}]
        criteria += [{"id": f"c{i + 1}", "kind": "constraint", "text": c, "result": "pending"}
                     for i, c in enumerate(constraints)]
        return cls({"task_id": task_id, "goal": goal, "constraints": constraints,
                    "acceptance_criteria": criteria, "status": STATUS_IN_PROGRESS,
                    "channel_plan": channel_plan,
                    "created_at": time.time(), "updated_at": time.time()})

    @classmethod
    def from_resumed(cls, prev_state: dict, new_task_id: str) -> "TaskContext":
        """断点续跑:约束与验收条件以**已落盘快照**为权威(调用方无法借恢复放宽),
        进度类字段从快照继承;task_id 换成本次运行的新会话。"""
        if not isinstance(prev_state, dict) or not prev_state.get("task_id"):
            raise ValueError("恢复快照无效")
        prev_task_id = _clip(prev_state.get("task_id"), 128)
        data = dict(prev_state)
        data["task_id"] = _clip(new_task_id, 128)
        data["resumed_from"] = prev_task_id
        data["status"] = STATUS_IN_PROGRESS
        data["current_focus"] = ""
        data["created_at"] = time.time()
        data["updated_at"] = time.time()
        return cls(data)

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        return copy.deepcopy(self._data)

    @staticmethod
    def _norm_criteria(raw) -> list:
        out = []
        for c in (raw or [])[:_LIST_LIMIT]:
            if not isinstance(c, dict):
                continue
            kind = str(c.get("kind") or "constraint")
            result = str(c.get("result") or "pending")
            out.append({"id": _clip(c.get("id"), 64),
                        "kind": kind if kind in CRITERION_KINDS else "constraint",
                        "text": _clip(c.get("text"), _FACT),
                        "result": result if result in CRITERION_RESULTS else "pending"})
        return out

    # ---------- 只读视图 ----------

    @property
    def task_id(self) -> str:
        return self._data["task_id"]

    @property
    def goal(self) -> str:
        return self._data["goal"]

    @property
    def status(self) -> str:
        return self._data["status"]

    @property
    def resumed_from(self) -> str:
        return self._data["resumed_from"]

    @property
    def constraints(self) -> tuple:
        return tuple(self._data["constraints"])

    @property
    def channel_plan(self) -> dict:
        return dict(self._data["channel_plan"])

    @property
    def acceptance_criteria(self) -> list:
        return copy.deepcopy(self._data["acceptance_criteria"])

    # ---------- 窄更新 API(进度类) ----------

    def _touch(self):
        self._data["updated_at"] = time.time()

    def set_status(self, status: str):
        self._data["status"] = _clip(status, 32) or STATUS_IN_PROGRESS
        self._touch()

    def set_focus(self, focus: str):
        self._data["current_focus"] = _clip(focus, _FACT)
        self._touch()

    def mark_completed(self, item: str):
        self._data["completed"] = _bounded(
            self._data["completed"] + [{"text": _clip(item, _FACT), "ts": time.time()}])
        self._touch()

    def add_pending(self, item: str):
        self._data["pending"] = _bounded(
            self._data["pending"] + [{"text": _clip(item, _FACT), "ts": time.time()}])
        self._touch()

    def add_fact(self, fact: str, source: str = ""):
        """执行中发现的事实:必须带来源,不与模型推测混写。"""
        self._data["discovered_facts"] = _bounded(
            self._data["discovered_facts"]
            + [{"fact": _clip(fact, _FACT), "source": _clip(source, 128),
                "ts": time.time()}])
        self._touch()

    def add_probe(self, channel: str, ok: bool, detail: str = ""):
        """【G4】能力探测结果(证据,非授权):只由 channel_policy.record_probe 落账。"""
        self._data["channel_probes"] = _bounded(
            self._data["channel_probes"]
            + [{"channel": channel, "ok": bool(ok), "detail": _clip(detail, 200),
                "ts": time.time()}])
        self._touch()

    def record_conflict(self, description: str, evidence: str = ""):
        """冲突不擅自消歧:保留双方证据,交由上层决策(用户指令优先于模型推测)。"""
        self._data["unresolved_conflicts"] = _bounded(
            self._data["unresolved_conflicts"]
            + [{"description": _clip(description, _FACT), "evidence": _clip(evidence, _FACT),
                "ts": time.time()}])
        self._touch()

    def add_artifact(self, kind: str, ref: str):
        self._data["artifacts"] = _bounded(
            self._data["artifacts"] + [{"kind": _clip(kind, 32), "ref": _clip(ref, _FACT),
                                        "ts": time.time()}])
        self._touch()

    def set_last_verified_state(self, desc: str):
        self._data["last_verified_state"] = _clip(desc, _FACT)
        self._touch()

    def update_criterion_result(self, criterion_id: str, result: str) -> bool:
        """只允许更新验证结果,不允许改验收条件的文本/类别(基准 §L4:不得中途放宽)。"""
        if result not in CRITERION_RESULTS:
            return False
        for c in self._data["acceptance_criteria"]:
            if c["id"] == criterion_id:
                c["result"] = result
                self._touch()
                return True
        return False
