"""
EventSpine —— 全系统唯一的进程内事件脊柱(P0 第二项:统一事件总线)。

审计结论:此前 4 套并存且不互通的发布订阅(HarnessEventBus / SystemBus /
HKC EventBus / steering_queue),WS 只桥接了 harness 一套 → truth/process 等
系统事件根本到不了前端。EventSpine 把它们收敛成一条脊柱 + 若干"投影"(projection):
WS / CLI / robot / edge 都只是订阅脊柱的投影,不再各自接线。

设计:
  · 主题(topic)用 `domain.entity.action` 命名;subscribe 支持**前缀匹配**
    (订阅 "harness" 命中 "harness.drift.blocked";订阅 "" 命中一切)。
  · project(sink):sink 收到**每一条**事件(WS/robot/edge 的统一出口)。
  · 并发纪律与 SystemBus 一致:锁只护 history + 订阅表快照,handler 在锁外执行,
    保证全局锁序恒为 spine→(pm/hmr),无反向嵌套 → 无死锁。
  · Event 刻意提供 .channel/.payload/.to_dict(),与旧 BusMessage 同形,
    使 SystemBus 可退化为本脊柱的薄门面而不改任何既有消费者。
"""
from __future__ import annotations
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Event:
    topic: str
    payload: dict
    ts: float = field(default_factory=time.time)
    source: str = ""
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def channel(self) -> str:
        return self.topic

    def to_dict(self) -> dict:
        return {"channel": self.topic, "topic": self.topic, "payload": self.payload,
                "ts": self.ts, "source": self.source, "correlation_id": self.correlation_id}


class EventSpine:
    """进程内唯一事件脊柱。发布/订阅(前缀匹配) + 投影 + 有界历史。线程安全。"""

    def __init__(self, history_limit: int = 1000):
        self._subs: list[tuple[str, Callable]] = []
        self._projections: list[Callable] = []
        self._history: deque = deque(maxlen=history_limit)
        self._lock = threading.RLock()

    def subscribe(self, topic_prefix: str, handler: Callable):
        """订阅某主题(前缀匹配)。handler(event) 在发布线程、锁外被调用。"""
        with self._lock:
            self._subs.append((topic_prefix or "", handler))

    def project(self, sink: Callable):
        """注册投影:每一条事件都会送给 sink(event)。用于 WS / robot / edge 统一出口。"""
        with self._lock:
            self._projections.append(sink)

    def publish(self, topic: str, payload: dict | None = None, source: str = "",
                correlation_id: str | None = None) -> Event:
        if correlation_id:
            evt = Event(topic=topic, payload=payload or {}, source=source,
                        correlation_id=correlation_id)
        else:
            evt = Event(topic=topic, payload=payload or {}, source=source)
        with self._lock:
            self._history.append(evt)
            handlers = [h for pfx, h in self._subs if topic.startswith(pfx)]
            projections = list(self._projections)
        for h in handlers:
            try:
                h(evt)
            except Exception as e:
                print(f"[EventSpine] handler error on '{topic}': {e}")
        for sink in projections:
            try:
                sink(evt)
            except Exception as e:
                print(f"[EventSpine] projection error on '{topic}': {e}")
        return evt

    def history(self, topic_prefix: Optional[str] = None, limit: int = 50) -> list:
        with self._lock:
            items = [e for e in self._history
                     if topic_prefix is None or e.topic.startswith(topic_prefix)]
        return [e.to_dict() for e in items[-limit:]]

    def stats(self) -> dict:
        with self._lock:
            return {"subscriptions": len(self._subs),
                    "projections": len(self._projections),
                    "history_size": len(self._history),
                    "history_limit": self._history.maxlen}
