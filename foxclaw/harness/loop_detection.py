"""
Foxclaw Harness — 工具循环检测 (Tool-Loop Detection)

借鉴 OpenClaw 的循环检测思路,适配 Foxclaw 的 Harness 体系。
断路器管"失败",循环检测管"无进展死循环"——Agent 不报错却陷入
重复/横跳,白烧 token。这是行为异常的一种,与 Harness 漂移检测同源。

三种循环模式:
  repeat    同一调用(工具+参数签名)连续重复 N 次
  ping_pong 两个调用交替出现(A→B→A→B)
  no_progress 同工具反复调用但结果无变化(轮询卡死)
多级:warning(预警)→ critical(严重)→ 熔断(建议中止)
"""
from __future__ import annotations
import hashlib
import json
import threading
from collections import deque
from dataclasses import dataclass


@dataclass
class LoopVerdict:
    stuck: bool = False
    level: str = "ok"             # ok / warning / critical
    detector: str = ""           # repeat / ping_pong / no_progress
    count: int = 0
    message: str = ""

    def to_dict(self) -> dict:
        return {"stuck": self.stuck, "level": self.level,
                "detector": self.detector, "count": self.count,
                "message": self.message}


@dataclass
class _ToolCall:
    signature: str               # 工具名+参数的指纹
    tool_name: str
    result_hash: str = ""        # 结果指纹(判 no_progress)


class ToolLoopDetector:
    """监控工具调用历史,检测无进展死循环。每个 Agent/会话一个实例。"""

    def __init__(self, history_size: int = 30, warning: int = 6,
                 critical: int = 12, circuit_break: int = 20):
        # 阈值单调递增保证(warning < critical < circuit_break)
        self.warning = max(2, warning)
        self.critical = max(self.warning + 1, critical)
        self.circuit_break = max(self.critical + 1, circuit_break)
        self.history: deque = deque(maxlen=max(history_size, self.circuit_break))
        self._last_verdict: LoopVerdict | None = None
        # 【深审·线程安全】SkillAdapter 持单个共享实例,invoke(含管线内 dispatch)可被并发任务
        # 同时调 record → history.append + _detect(list(history)) 非原子,需锁(线程安全铁律)。
        self._lock = threading.RLock()

    @staticmethod
    def _signature(tool_name: str, args: dict) -> str:
        """工具名 + 规范化参数 → 指纹。"""
        try:
            payload = json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
        except Exception:
            payload = str(args)
        return hashlib.sha256(f"{tool_name}::{payload}".encode()).hexdigest()[:16]

    @staticmethod
    def _result_sig(result) -> str:
        try:
            s = json.dumps(result, sort_keys=True, ensure_ascii=False)[:500]
        except Exception:
            s = str(result)[:500]
        return hashlib.sha256(s.encode()).hexdigest()[:16]

    def record(self, tool_name: str, args: dict | None = None, result=None) -> LoopVerdict:
        """记录一次工具调用并即时检测。返回 LoopVerdict。"""
        sig = self._signature(tool_name, args or {})
        rhash = self._result_sig(result) if result is not None else ""
        with self._lock:                      # append + _detect 必须原子(并发 record 会交错)
            self.history.append(_ToolCall(sig, tool_name, rhash))
            verdict = self._detect()
            self._last_verdict = verdict
            return verdict

    def _level_for(self, count: int) -> str:
        if count >= self.circuit_break:
            return "critical"      # 达熔断阈值仍按 critical 等级(由 stuck+建议中止体现)
        if count >= self.critical:
            return "critical"
        if count >= self.warning:
            return "warning"
        return "ok"

    def _detect(self) -> LoopVerdict:
        h = list(self.history)
        if len(h) < self.warning:
            return LoopVerdict()

        # 1. repeat / no_progress:末尾连续相同签名
        last_sig = h[-1].signature
        streak = 0
        for c in reversed(h):
            if c.signature == last_sig:
                streak += 1
            else:
                break
        if streak >= self.warning:
            lvl = self._level_for(streak)
            # no_progress 优先:同签名重复 且 (有结果的那些)结果都相同
            # (只比对非空 result_hash,避免混合有/无 result 时漏判轮询卡死)
            streak_calls = h[-streak:]
            with_result = [c.result_hash for c in streak_calls if c.result_hash]
            if len(with_result) >= 2 and len(set(with_result)) == 1:
                return LoopVerdict(
                    stuck=True, level=lvl, detector="no_progress", count=streak,
                    message=(f"工具「{h[-1].tool_name}」轮询 {streak} 次结果无变化"
                             + ("(已达熔断阈值,建议中止)" if streak >= self.circuit_break else "")))
            # 否则按 repeat
            return LoopVerdict(
                stuck=True, level=lvl, detector="repeat", count=streak,
                message=(f"工具「{h[-1].tool_name}」连续重复调用 {streak} 次"
                         + ("(已达熔断阈值,建议中止)" if streak >= self.circuit_break else "")))

        # 3. ping_pong:末尾两签名交替(A→B→A→B)
        if len(h) >= 4:
            a, b = h[-1].signature, h[-2].signature
            if a != b:
                alt = 0
                expect = [a, b]
                for i, c in enumerate(reversed(h)):
                    if c.signature == expect[i % 2]:
                        alt += 1
                    else:
                        break
                if alt >= self.warning:
                    return LoopVerdict(
                        stuck=True, level=self._level_for(alt),
                        detector="ping_pong", count=alt,
                        message=(f"工具「{h[-1].tool_name}」与「{h[-2].tool_name}」"
                                 f"交替横跳 {alt} 次,无进展"))

        return LoopVerdict()

    def should_abort(self) -> bool:
        """是否建议中止(达到熔断阈值)。复用最近一次 record 的检测结果。"""
        with self._lock:
            v = self._last_verdict or self._detect()
            return v.stuck and v.count >= self.circuit_break

    def reset(self):
        with self._lock:
            self.history.clear()
            self._last_verdict = None
