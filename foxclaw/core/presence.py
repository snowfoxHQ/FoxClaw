"""会话活跃信号(C1 无人值守协议)——「有没有人在看」的一等管线值。

形态:进程内 PresenceTracker,记每个会话键(渠道会话为 `channel:conversation:sender`,
web/API 会话为其 session_id)最后一次**用户侧活动**时刻;`attended()` 按窗口判定。

约定:
  · 渠道消息到达即 touch——消息刚到 = 用户此刻在场,本条任务的确认环节按「有人在看」走;
  · 定时/后台/API 直调路径没有 touch → 超窗即无人值守;
  · 长任务中途的确认发生在任务运行时:若用户下发任务后离开,超窗后 attended=False,
    显式批准档动作转化为「摆出决策 + 停」(协议语义见 behavior.yaml:unattended_protocol)。
窗口:FOXCLAW_ATTENDED_WINDOW(秒,默认 300)。
"""
from __future__ import annotations

import os
import threading
import time

_WINDOW_ENV = "FOXCLAW_ATTENDED_WINDOW"
DEFAULT_WINDOW = 300.0


def default_window() -> float:
    try:
        w = float(os.environ.get(_WINDOW_ENV, DEFAULT_WINDOW))
        return w if w > 0 else DEFAULT_WINDOW
    except (TypeError, ValueError):
        return DEFAULT_WINDOW


class PresenceTracker:
    """线程安全的会话活跃记录。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._last_seen: dict = {}

    def touch(self, session_id: str, now: float | None = None) -> float:
        now = time.time() if now is None else now
        sid = str(session_id or "")
        if not sid:
            return now
        with self._lock:
            self._last_seen[sid] = now
        return now

    def attended(self, session_id: str, window: float | None = None,
                 now: float | None = None) -> bool:
        """该会话是否有人在看:窗口内有过用户活动即 True;无记录即 False(fail-closed)。"""
        sid = str(session_id or "")
        if not sid:
            return False
        window = default_window() if window is None else float(window)
        now = time.time() if now is None else now
        with self._lock:
            last = self._last_seen.get(sid)
        if last is None:
            return False
        return (now - last) <= window

    def last_seen(self, session_id: str) -> float:
        with self._lock:
            return self._last_seen.get(str(session_id or ""), 0.0)

    def forget(self, session_id: str) -> None:
        with self._lock:
            self._last_seen.pop(str(session_id or ""), None)


_tracker = PresenceTracker()


def touch(session_id: str) -> float:
    return _tracker.touch(session_id)


def attended(session_id: str, window: float | None = None) -> bool:
    return _tracker.attended(session_id, window)


def last_seen(session_id: str) -> float:
    return _tracker.last_seen(session_id)


def forget(session_id: str) -> None:
    _tracker.forget(session_id)
