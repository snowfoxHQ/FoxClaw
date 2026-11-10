"""Harness 事件总线（观测优先）。"""
from typing import Callable


class HarnessEventBus:
    """阶段一：事件总线（观测优先）"""

    _SPINE_SKIP = {"goal_registered"}

    def __init__(self, spine=None, source: str = ""):
        self._handlers: dict[str, list[Callable]] = {}
        self._spine = spine
        self._source = source

    def on(self, event: str, handler: Callable):
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, data: dict):
        for h in self._handlers.get(event, []):
            try:
                h(data)
            except Exception as e:
                print(f"[Harness EventBus] handler error: {e}")
        if self._spine is not None and event not in self._SPINE_SKIP:
            try:
                self._spine.publish(event, data, source=self._source or "harness")
            except Exception:
                pass
