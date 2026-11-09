"""
系统事件总线 (SystemBus)

进程内发布订阅总线,用于系统级事件流转(执行→审计→记忆)。
借鉴 Codex 版 Foxclaw 的 SystemBus,提升为本项目的全局事件中枢。

与 HarnessEventBus 的区别:HarnessEventBus 局限于单个 Agent 的 Harness 内部;
SystemBus 是系统级的,跨 Agent / 跨模块,且保留事件历史供审计回放。
"""
from __future__ import annotations
import time
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
    同形(都有 .channel/.payload/.to_dict())。

    【P3-6/P4-1】subscribe/history 加 exact 参数,语义显式化:
      · exact=False(默认):前缀匹配(EventSpine 原生行为)。订阅 "execution.completed"
        会同时收到 "execution.completed" 和 "execution.completed.extra"(如果有)。
        旧版独立 SystemBus 是精确匹配,门面化后语义悄悄变了——精确 channel 名的
        订阅点应显式传 exact=True,前缀意图保持默认。
      · exact=True:包一层过滤,只放行 topic 完全相等的事件(与旧版语义一致)。

    【P4-1】调用点审查结论(grep `.subscribe(` 全仓):
      ✓ truth_pipeline.py ×3(execution.completed/trace.recorded/truth.appended)→ exact=True
      ✓ foxclaw/__init__.py(truth.appended → TruthCrystallizer)→ exact=True
      ✓ cognitive/state.py ×2(execution.completed/memory.updated)→ exact=True
      · cognitive/state.py(task.lifecycle)→ 前缀意图(注释明示),保持 exact=False
      · core/audit_sink.py(memory.shared.write)→ 直接持 spine 引用,不经本门面,不在范围
      · knowledge/__init__.py → HKC 自有 event bus,非本总线,不在范围
    """

    def __init__(self, history_limit: int = 500, spine=None):
        if spine is None:
            from ..kernel.spine import EventSpine
            spine = EventSpine(history_limit=history_limit)
        self.spine = spine

    def subscribe(self, channel: str, handler: Callable, exact: bool = False):
        """订阅 channel。

        exact=False(默认):前缀匹配。subscribe("execution") 收到
            execution.completed / execution.failed 等。
        exact=True:精确匹配。subscribe("execution.completed", exact=True)
            只收到 topic 完全等于 "execution.completed" 的事件。
        """
        if exact:
            def _exact_handler(msg):
                if getattr(msg, "topic", None) == channel:
                    handler(msg)
            self.spine.subscribe(channel, _exact_handler)
        else:
            self.spine.subscribe(channel, handler)

    def publish(self, channel: str, payload: dict):
        return self.spine.publish(channel, payload, source="system_bus")

    def history(self, channel: str | None = None, limit: int = 50,
                exact: bool = False) -> list:
        """历史事件(dict 列表)。exact=True 时只返回 topic 完全匹配的事件
        (默认前缀匹配,与 EventSpine 原生一致)。"""
        events = self.spine.history(channel, limit)
        if exact and channel:
            events = [e for e in events
                      if (e.get("topic") or e.get("channel")) == channel]
        return events
