"""
认知状态 (CognitiveState)

把"系统此刻在想什么"显式建模成一个可快照的认知状态。
借鉴 Codex 版 Foxclaw 的 CognitiveState,适配本项目:不新存一份数据,而是
作为聚合器 + 事件订阅者,把散落在调度器/进程/记忆/路由里的信号,聚成一张
"系统当前心智状态"快照。

对齐"认知操作系统"定位:OS 有 system state,认知 OS 该有 cognitive state。
可驱动前端"系统在想什么"实时面板,也用于调试与回放。
"""
from __future__ import annotations

from typing import Optional
import time
from dataclasses import dataclass, field


@dataclass
class CognitiveState:
    """系统当前认知状态快照。随事件总线上的活动自动更新。"""

    active_goal: Optional[str] = None
    active_agent: Optional[str] = None
    semantic_focus: Optional[str] = None
    attention_scope: list = field(default_factory=list)
    reasoning_depth: float = 0.0
    confidence_map: dict = field(default_factory=dict)
    activated_memories: list = field(default_factory=list)
    last_route: Optional[str] = None
    active_processes: int = 0
    temporal_window: str = "current_turn"
    updated_at: float = field(default_factory=time.time)


    def on_execution(self, payload: dict):
        """执行事件 → 更新目标/Agent/注意力。"""
        payload = payload or {}
        task = payload.get("task", "")
        self.active_goal = task[:120] if task else self.active_goal
        self.active_agent = payload.get("agent_id") or self.active_agent
        self.attention_scope = self._keywords(task)
        sched = payload.get("schedule") or {}
        if sched:
            self.semantic_focus = sched.get("intent")
            self.last_route = sched.get("route_hint")
            self.confidence_map["priority"] = sched.get("priority", 0.0)
        self.temporal_window = "current_turn"
        self.updated_at = time.time()

    def on_memory_updated(self, payload: dict):
        """记忆更新事件 → 记录激活的记忆(去重,保留最近 5 条)。"""
        payload = payload or {}
        mu = payload.get("memory_update") or {}
        content = mu.get("content")
        if content:
            snippet = content[:80]
            existing = [m for m in self.activated_memories if m != snippet]
            self.activated_memories = [snippet] + existing[:4]
            self.temporal_window = "memory_augmented"
        self.updated_at = time.time()

    def on_process_change(self, payload: dict, active_count: int | None = None):
        """进程状态变更 → 更新活跃进程数。"""
        if active_count is not None:
            self.active_processes = active_count
        self.updated_at = time.time()

    @staticmethod
    def _keywords(text: str, n: int = 6) -> list:
        import re
        rough = [w for w in re.split(r"[\s,，、。!?:;]", text or "") if len(w) >= 2]
        out = []
        for seg in rough:
            if len(seg) <= 4:
                out.append(seg)
            else:
                out.append(seg[:3])
                if len(seg) > 6:
                    out.append(seg[len(seg)//2:len(seg)//2+2])
                out.append(seg[-2:])
        return out[:n]

    def snapshot(self) -> dict:
        return {
            "active_goal": self.active_goal,
            "active_agent": self.active_agent,
            "semantic_focus": self.semantic_focus,
            "attention_scope": list(self.attention_scope),
            "reasoning_depth": self.reasoning_depth,
            "confidence_map": dict(self.confidence_map),
            "activated_memories": list(self.activated_memories),
            "last_route": self.last_route,
            "active_processes": self.active_processes,
            "temporal_window": self.temporal_window,
            "updated_at": self.updated_at,
        }


class CognitiveStateTracker:
    """把 CognitiveState 接到事件总线 + 进程管理器,自动维护快照。"""

    def __init__(self, bus, process_manager=None):
        import threading
        self.state = CognitiveState()
        self.process_manager = process_manager
        self._lock = threading.Lock()
        if bus is not None:
            bus.subscribe("execution.completed", self._on_exec, exact=True)
            bus.subscribe("memory.updated", self._on_mem, exact=True)
            bus.subscribe("task.lifecycle", self._on_proc)

    def _on_exec(self, msg):
        if msg is None:
            return
        with self._lock:
            self.state.on_execution(getattr(msg, "payload", None) or {})

    def _on_mem(self, msg):
        if msg is None:
            return
        with self._lock:
            self.state.on_memory_updated(getattr(msg, "payload", None) or {})

    def _on_proc(self, msg):
        if msg is None:
            return
        active = None
        pm = self.process_manager
        if pm is not None:
            if hasattr(pm, "active_count"):
                active = pm.active_count()
            else:
                st = pm.stats().get("by_state", {})
                active = st.get("scheduled", 0) + st.get("running", 0)
        with self._lock:
            self.state.on_process_change(getattr(msg, "payload", None) or {}, active)

    def snapshot(self) -> dict:
        with self._lock:
            return self.state.snapshot()
