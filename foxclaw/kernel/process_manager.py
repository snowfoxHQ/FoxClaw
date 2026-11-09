"""
进程管理器 (ProcessManager)

把每个任务建模成操作系统的"进程",管理其完整生命周期:
  created → queued → scheduled → running → completed / failed

借鉴 Codex 版 Foxclaw 的 ProcessManager,适配本项目:
- 与认知调度器结合:进程携带调度决策(优先级/路由)
- 与真值管线结合:状态变更可发事件
- 保留 state_history:每个进程的完整状态轨迹可追溯(对齐可观测原则)

这让"任务执行"从一次性函数调用,变成可观测、可追溯的"进程生命周期"。
"""
from __future__ import annotations
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


_VALID_TRANSITIONS = {
    "created":   {"queued", "failed"},
    "queued":    {"scheduled", "failed"},
    "scheduled": {"running", "failed"},
    "running":   {"completed", "failed"},
    "completed": set(),
    "failed":    set(),
}
_TERMINAL = {"completed", "failed"}


@dataclass
class ProcessRecord:
    task: str
    agent_id: Optional[str] = None
    state: str = "created"
    process_id: str = field(default_factory=lambda: f"proc_{uuid.uuid4().hex[:8]}")
    priority: float = 0.0
    route_hint: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    state_history: list = field(default_factory=list)
    fail_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "process_id": self.process_id, "task": self.task,
            "agent_id": self.agent_id, "state": self.state,
            "priority": self.priority, "route_hint": self.route_hint,
            "state_history": self.state_history, "fail_reason": self.fail_reason,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }


class ProcessManager:
    """进程生命周期管理。每个任务一个进程,状态受状态机约束。"""

    def __init__(self, bus=None, history_limit: int = 1000):
        self._processes: dict[str, ProcessRecord] = {}
        self.bus = bus
        self._history_limit = history_limit
        self._active = 0
        self._lock = threading.RLock()

    def create(self, task: str, agent_id: str | None = None,
               schedule: dict | None = None) -> ProcessRecord:
        """创建进程(任务进入系统)。携带调度决策。"""
        rec = ProcessRecord(
            task=task, agent_id=agent_id,
            priority=(schedule or {}).get("priority", 0.0),
            route_hint=(schedule or {}).get("route_hint"),
        )
        with self._lock:
            rec.state_history.append("created")
            self._transition(rec, "queued")
            if schedule:
                self._transition(rec, "scheduled")
            self._processes[rec.process_id] = rec
            self._active += 1
            self._evict_if_needed()
            self._emit(rec)
        return rec

    def mark_running(self, process_id: str) -> Optional[ProcessRecord]:
        with self._lock:
            rec = self._processes.get(process_id)
            if rec is None:
                return None
            if rec.state == "queued":
                self._transition(rec, "scheduled")
            return self._safe_transition(process_id, "running")

    def mark_completed(self, process_id: str) -> Optional[ProcessRecord]:
        with self._lock:
            return self._safe_transition(process_id, "completed")

    def mark_failed(self, process_id: str, reason: str = "") -> Optional[ProcessRecord]:
        with self._lock:
            rec = self._processes.get(process_id)
            if rec is None:
                return None
            if rec.state in _TERMINAL:
                return rec
            self._transition(rec, "failed")
            rec.fail_reason = reason
            self._emit(rec)
            return rec

    def get(self, process_id: str) -> Optional[ProcessRecord]:
        with self._lock:
            return self._processes.get(process_id)

    def list(self, state: str | None = None, limit: int = 50) -> list:
        with self._lock:
            items = list(self._processes.values())
        if state:
            items = [r for r in items if r.state == state]
        items.sort(key=lambda r: r.created_at, reverse=True)
        return [r.to_dict() for r in items[:limit]]

    def active_count(self) -> int:
        """活跃(非终态:queued/scheduled/running/created)进程数,O(1) 增量维护。
        供高频 lifecycle 事件消费(免去每次 stats() 遍历全部进程)。"""
        with self._lock:
            return self._active

    def stats(self) -> dict:
        with self._lock:
            counts: dict[str, int] = {}
            for r in self._processes.values():
                counts[r.state] = counts.get(r.state, 0) + 1
            return {"total": len(self._processes), "by_state": counts,
                    "active": self._active}

    def _safe_transition(self, process_id: str, next_state: str) -> Optional[ProcessRecord]:
        rec = self._processes.get(process_id)
        if rec is None:
            return None
        if rec.state in _TERMINAL:
            return rec
        if rec.state == next_state:
            return rec
        self._transition(rec, next_state)
        self._emit(rec)
        return rec

    def _transition(self, rec: ProcessRecord, next_state: str):
        """受状态机约束的状态流转。真正非法的跳转(如跳过中间态)抛错 fail-fast。

        【P5-3】终态守卫:已是终态的进程再流转 → 幂等忽略(不改状态、不重复 -1)。
        此前"不会重复 -1"完全依赖上游 _safe_transition/mark_failed 的守卫——若未来
        新增调用点直接调本方法,活跃计数会被重复扣减(可扣成负数,stats 失真)。
        这里是双保险。注意行为变更:终态 → 任何状态,从抛 ValueError 变为静默忽略。
        """
        if rec.state in _TERMINAL:
            return
        allowed = _VALID_TRANSITIONS.get(rec.state, set())
        if next_state not in allowed:
            raise ValueError(
                f"非法状态流转: {rec.state} → {next_state}(进程 {rec.process_id})")
        if next_state in _TERMINAL:
            self._active -= 1
        rec.state = next_state
        rec.updated_at = time.time()
        rec.state_history.append(next_state)

    def _emit(self, rec: ProcessRecord):
        if self.bus is not None:
            try:
                self.bus.publish(f"task.lifecycle.{rec.state}", {
                    "process_id": rec.process_id, "state": rec.state, "phase": rec.state,
                    "task": rec.task, "agent_id": rec.agent_id,
                })
            except Exception:
                pass

    def _evict_if_needed(self):
        """超出上限时,清理最老的终态进程(保留活跃进程)。"""
        if len(self._processes) <= self._history_limit:
            return
        terminal = [(pid, r) for pid, r in self._processes.items()
                    if r.state in _TERMINAL]
        terminal.sort(key=lambda x: x[1].created_at)
        for pid, _ in terminal[:len(self._processes) - self._history_limit]:
            del self._processes[pid]
