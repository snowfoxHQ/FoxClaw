"""
Scheduler —— 微内核的任务调度器(v2 ① Step1:准入 + 记账 + 生命周期事件)。

审计:调度散在主管 _schedule / AHS / SteeringQueue,无全局并发配额/背压。
本调度器把"任务准入"收敛到一处:并发配额(信号量)+ 记账 + task.lifecycle.* 事件。

**为什么用线程级 Semaphore 而非 asyncio**:本系统是多线程/多事件循环模型
(每请求 `to_thread + asyncio.run` 各自独立事件循环,见 api.py)。asyncio 原语绑定单一
事件循环、无法跨循环共享;线程级 Semaphore 跨线程/跨循环通用,契合既有模型。

Step1 范围(behavior-preserving):
  · 默认 max_concurrency=None → **无上限**,run() 直通,行为与现在逐字一致;
  · 设了上限 → 超额任务在 run() 里阻塞等待空位(背压);
  · 每个任务 emit task.lifecycle.{scheduled,started,completed,failed} + 记账(供 /api/scheduler)。
优先级 v0 只**记录**(FIFO 准入);严格优先级/抢占留 Step5。
"""
from __future__ import annotations
import heapq
import threading
from typing import Any, Callable, Optional


class _PriorityAdmission:
    """并发准入(Step5):最多 max 个在跑;超额者**按优先级排队**(高优先先获空位)。
    这是"下一个跑谁"的准入优先级——**不抢占正在跑的任务**(线程模型下杀线程不安全)。
    max=None → 无限制、零等待(behavior-preserving)。"""

    def __init__(self, max_slots: Optional[int]):
        self.max = max_slots
        self._free = max_slots or 0
        self._cv = threading.Condition()
        self._heap: list = []          # (-priority, seq):优先级降序,同优先级 FIFO
        self._seq = 0

    def acquire(self, priority: int = 0) -> None:
        if self.max is None:
            return
        with self._cv:
            self._seq += 1
            ticket = (-priority, self._seq)
            heapq.heappush(self._heap, ticket)
            while not (self._free > 0 and self._heap[0] == ticket):
                self._cv.wait()
            heapq.heappop(self._heap)
            self._free -= 1
            if self._free > 0 and self._heap:   # 还有空槽+等待者 → 唤醒下一个最高优先者
                self._cv.notify_all()

    def release(self) -> None:
        if self.max is None:
            return
        with self._cv:
            self._free += 1
            self._cv.notify_all()


class Scheduler:
    def __init__(self, max_concurrency: Optional[int] = None, spine=None,
                 process_manager=None):
        self.max_concurrency = max_concurrency if (max_concurrency and max_concurrency > 0) else None
        # Step5:优先级准入(满载时高优先排队者先获空位;不抢占正在跑的)。无上限时零等待。
        self._adm = _PriorityAdmission(self.max_concurrency)
        self._spine = spine
        # 【Step3】统一生命周期:有 ProcessManager 则委托它发 task.lifecycle.*(唯一词汇、
        # 唯一状态机);无 pm(standalone/测试)则调度器自发 task.lifecycle.* 作回退。
        self.process_manager = process_manager
        self._lock = threading.Lock()
        self._stats = {"submitted": 0, "running": 0, "completed": 0,
                       "failed": 0, "peak_running": 0}

    def run(self, fn: Callable[[], Any], priority: int = 0,
            agent_id: str = "", task: str = "") -> Any:
        """准入受控地执行 fn(同步可调用,通常是 `lambda: asyncio.run(os.run(...))`)。
        有并发上限时先获取空位(阻塞=背压),跑完释放;记账由本调度器,生命周期事件由 pm(或回退)。"""
        pm = self.process_manager
        with self._lock:
            self._stats["submitted"] += 1
        proc = None
        if pm is not None:
            proc = pm.create(task, agent_id=agent_id, schedule={"priority": priority})
        else:
            self._emit("scheduled", agent_id, task, priority)
        # 优先级准入:满载则排队等空位(高优先先入);无上限时零等待(behavior-preserving)
        self._adm.acquire(priority)
        try:
            with self._lock:
                self._stats["running"] += 1
                if self._stats["running"] > self._stats["peak_running"]:
                    self._stats["peak_running"] = self._stats["running"]
            if proc is not None:
                pm.mark_running(proc.process_id)
            else:
                # 【P5-2】回退路径的词汇与 ProcessManager 对齐:started → running
                # (pm 的词汇是 created/queued/scheduled/running/completed/failed)
                self._emit("running", agent_id, task, priority)
            result = fn()
            with self._lock:
                self._stats["completed"] += 1
            if proc is not None:
                pm.mark_completed(proc.process_id)
            else:
                self._emit("completed", agent_id, task, priority)
            return result
        except Exception:
            with self._lock:
                self._stats["failed"] += 1
            if proc is not None:
                pm.mark_failed(proc.process_id, reason="error")
            else:
                self._emit("failed", agent_id, task, priority)
            raise
        finally:
            with self._lock:
                self._stats["running"] -= 1
            self._adm.release()

    def stats(self) -> dict:
        with self._lock:
            s = dict(self._stats)
        s["max_concurrency"] = self.max_concurrency
        return s

    def _emit(self, phase: str, agent_id: str, task: str, priority: int) -> None:
        if self._spine is None:
            return
        try:
            self._spine.publish(f"task.lifecycle.{phase}", {
                "agent_id": agent_id, "task": (task or "")[:120],
                "priority": priority, "phase": phase}, source="scheduler")
        except Exception:
            pass
