"""
Foxclaw Core — 运行中干预队列 (Steering Queue)

借鉴 OpenClaw 的"租约队列"原理,适配 Foxclaw。解决的问题:
  子任务完成的结果,如何安全地回收给主任务——
  · 不丢:主任务崩了/没及时取,结果不能消失(租约过期 → 重新入队)
  · 不重复:已被某个消费者租走的结果,不会被另一个消费者再拿一次

当前用途:多 Agent 顺序执行其实用不上(直接收集即可)。
设计目标:为**未来并行子 Agent**预留——主管并行派发多个子任务,
各自完成时间不定,本队列负责"完成一个安全回收一个,不丢不重"。

租约(Lease)机制:
  · publish:子任务完成 → 结果入队(pending)
  · lease:消费者租走一批结果(标记 leased + 租约到期时间),拿去注入主任务
  · ack:消费者确认处理完 → 结果移除
  · 过期未 ack 的 leased 项 → 自动回到 pending(主任务崩溃/超时的兜底)
"""
from __future__ import annotations
import threading
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class SteeringItem:
    item_id: str
    requester: str                 # 谁在等这个结果(主任务/会话标识)
    source_agent: str              # 哪个子 Agent 产出的
    payload: dict                  # 结果内容
    created: float = field(default_factory=time.time)
    leased_until: float = 0.0      # 0 = 未租出;>0 = 租约到期时间戳
    lease_id: str = ""             # 当前租约持有者标识

    @property
    def is_leased(self) -> bool:
        return self.leased_until > time.time()

    def to_dict(self) -> dict:
        return {"item_id": self.item_id, "requester": self.requester,
                "source_agent": self.source_agent, "payload": self.payload,
                "created": self.created, "leased": self.is_leased}


class SteeringQueue:
    """租约队列。线程安全,支持多消费者并发租取不重复。"""

    def __init__(self, lease_seconds: float = 30.0):
        self.lease_seconds = lease_seconds
        self._items: dict[str, SteeringItem] = {}
        self._lock = threading.RLock()

    def publish(self, requester: str, source_agent: str, payload: dict) -> str:
        """子任务完成 → 结果入队。返回 item_id。"""
        with self._lock:
            item_id = uuid.uuid4().hex[:16]
            self._items[item_id] = SteeringItem(
                item_id=item_id, requester=requester,
                source_agent=source_agent, payload=payload)
            return item_id

    def lease(self, requester: str | None = None, max_items: int = 10) -> list[SteeringItem]:
        """租走一批待处理结果(可按 requester 过滤)。

        原子地:挑出未被租用(或租约已过期)的项,标记为 leased,返回。
        同一项不会被两个消费者同时租到(锁内完成挑选+标记)。
        """
        with self._lock:
            self._reclaim_expired()
            now = time.time()
            leased = []
            lease_id = uuid.uuid4().hex[:12]
            for item in self._items.values():
                if len(leased) >= max_items:
                    break
                if item.is_leased:
                    continue
                if requester is not None and item.requester != requester:
                    continue
                item.leased_until = now + self.lease_seconds
                item.lease_id = lease_id
                leased.append(item)
            return leased

    def ack(self, item_id: str, lease_id: str | None = None) -> bool:
        """消费者确认处理完 → 移除该项。

        校验 lease_id:只有持有当前租约的消费者能 ack(防止过期租约误删
        已被别人重新租走的项)。lease_id=None 时跳过校验(兼容简单场景)。
        """
        with self._lock:
            item = self._items.get(item_id)
            if item is None:
                return False
            if lease_id is not None and item.lease_id != lease_id:
                return False        # 租约不匹配(可能已过期被别人租走)
            del self._items[item_id]
            return True

    def nack(self, item_id: str):
        """消费者放弃处理 → 立即释放租约,回到 pending(可被重新租取)。"""
        with self._lock:
            item = self._items.get(item_id)
            if item:
                item.leased_until = 0.0
                item.lease_id = ""

    def _reclaim_expired(self):
        """过期未 ack 的 leased 项 → 回到 pending(主任务崩溃/超时的兜底)。"""
        now = time.time()
        for item in self._items.values():
            if 0 < item.leased_until <= now:
                item.leased_until = 0.0
                item.lease_id = ""

    def pending_count(self, requester: str | None = None) -> int:
        """待处理(未被有效租用)的项数。"""
        with self._lock:
            self._reclaim_expired()
            return sum(1 for it in self._items.values()
                       if not it.is_leased
                       and (requester is None or it.requester == requester))

    def stats(self) -> dict:
        with self._lock:
            self._reclaim_expired()
            total = len(self._items)
            leased = sum(1 for it in self._items.values() if it.is_leased)
            return {"total": total, "leased": leased, "pending": total - leased,
                    "lease_seconds": self.lease_seconds}

    def drain(self, requester: str) -> list[dict]:
        """便捷方法(顺序场景用):取出某 requester 的待处理结果并清空。

        适合"主任务等所有子任务完成后一次性收集"的顺序模式。
        尊重租约:**跳过正被 lease 租用的项**,避免与 lease/ack 混用时
        同一结果被处理两次(drain 拿一次 + 租约消费者再拿一次)。
        """
        with self._lock:
            self._reclaim_expired()
            out = []
            to_del = []
            for item_id, item in self._items.items():
                if item.requester != requester:
                    continue
                if item.is_leased:
                    continue        # 正被租用 → 留给租约消费者,drain 不碰
                out.append(item.payload)
                to_del.append(item_id)
            for item_id in to_del:
                del self._items[item_id]
            return out
