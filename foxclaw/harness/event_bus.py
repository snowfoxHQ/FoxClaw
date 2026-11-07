"""Harness 事件总线（观测优先）。"""
from typing import Callable


class HarnessEventBus:
    """阶段一：事件总线（观测优先）"""

    # 高频且纯内部的信号不上脊柱,避免 WS 噪音(见审计"哪些事件不应该广播")
    _SPINE_SKIP = {"goal_registered"}

    def __init__(self, spine=None, source: str = ""):
        self._handlers: dict[str, list[Callable]] = {}
        self._spine = spine        # 【P0·统一脊柱】非空则把 emit 同时转发进全局 EventSpine
        self._source = source      # 事件来源(agent_id),便于投影侧区分

    def on(self, event: str, handler: Callable):
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, data: dict):
        # 1) 本地订阅者(向后兼容:agent.harness.bus.on / harness 自订阅 / governance)
        for h in self._handlers.get(event, []):
            try:
                h(data)
            except Exception as e:
                print(f"[Harness EventBus] handler error: {e}")
        # 2) 汇入全局脊柱:harness 事件与系统事件走同一条脊柱、被同一 WS 投影观测
        if self._spine is not None and event not in self._SPINE_SKIP:
            try:
                self._spine.publish(event, data, source=self._source or "harness")
            except Exception:
                pass
