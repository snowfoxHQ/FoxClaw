"""
系统事件总线 (SystemBus)

进程内发布订阅总线,用于系统级事件流转(执行→审计→记忆)。
借鉴 Codex 版 HestiaOS 的 SystemBus,提升为本项目的全局事件中枢。

与 HarnessEventBus 的区别:HarnessEventBus 局限于单个 Agent 的 Harness 内部;
SystemBus 是系统级的,跨 Agent / 跨模块,且保留事件历史供审计回放。
"""
from __future__ import annotations
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class BusMessage:
    channel: str
    payload: dict
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"channel": self.channel, "payload": self.payload, "ts": self.ts}


class SystemBus:
    """系统级发布订阅总线。

    【P0·统一事件脊柱】SystemBus 现已退化为 EventSpine 的**薄门面**:系统级事件
    与 harness 事件汇入同一条脊柱,被同一个 WS 投影统一观测(修复"truth/process
    到不了前端")。对既有消费者(TruthPipeline/ProcessManager/CognitiveState/主管分发)
    零改动:publish/subscribe/history 签名不变;handler 收到的 Event 与旧 BusMessage
    同形(都有 .channel/.payload/.to_dict())。subscribe 以精确 channel 作前缀,语义等价。
    """

    def __init__(self, history_limit: int = 500, spine=None):
        if spine is None:
            from ..kernel.spine import EventSpine
            spine = EventSpine(history_limit=history_limit)
        self.spine = spine

    def subscribe(self, channel: str, handler: Callable):
        self.spine.subscribe(channel, handler)

    def publish(self, channel: str, payload: dict):
        return self.spine.publish(channel, payload, source="system_bus")

    def history(self, channel: str = None, limit: int = 50) -> list:
        return self.spine.history(channel, limit)
