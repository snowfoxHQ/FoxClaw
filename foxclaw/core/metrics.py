"""进程内轻量可观测(ops 监控):请求/LLM 的计数、错误率、延迟分位(p50/p95)。
无外部依赖、线程安全(仿 token_meter)。生产用 /api/metrics(JSON) 或 /metrics(Prometheus 文本)拉取。"""
from __future__ import annotations
import time
import threading
from collections import defaultdict, deque

_MAX_SAMPLES = 500   # 每个 key 保留最近 N 次延迟(有界,防内存涨)


class _Stat:
    __slots__ = ("count", "errors", "samples")

    def __init__(self):
        self.count = 0
        self.errors = 0
        self.samples: deque[float] = deque(maxlen=_MAX_SAMPLES)


def _pct(sorted_vals, p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = int(round((p / 100.0) * (len(sorted_vals) - 1)))
    return float(sorted_vals[max(0, min(k, len(sorted_vals) - 1))])


class MetricsCollector:
    """请求 + LLM 调用的计数/错误/延迟。record_* 高频调用(锁内 O(1))。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._ep: dict = defaultdict(_Stat)     # endpoint(路由模板) -> _Stat
        self._llm: dict = defaultdict(_Stat)    # provider -> _Stat
        self._start = time.time()

    def record_request(self, endpoint: str, status: int, ms: float):
        with self._lock:
            s = self._ep[endpoint]
            s.count += 1
            if status >= 500 or status == 0:
                s.errors += 1
            s.samples.append(ms)

    def record_llm(self, provider: str, ok: bool, ms: float):
        with self._lock:
            s = self._llm[provider or "?"]
            s.count += 1
            if not ok:
                s.errors += 1
            s.samples.append(ms)

    @staticmethod
    def _stat_dict(s: _Stat) -> dict:
        vals = sorted(s.samples)
        return {
            "count": s.count, "errors": s.errors,
            "error_rate": round(s.errors / s.count, 4) if s.count else 0.0,
            "p50_ms": round(_pct(vals, 50), 1),
            "p95_ms": round(_pct(vals, 95), 1),
            "avg_ms": round(sum(vals) / len(vals), 1) if vals else 0.0,
        }

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "uptime_s": round(time.time() - self._start, 1),
                "endpoints": {k: self._stat_dict(v) for k, v in self._ep.items()},
                "llm": {k: self._stat_dict(v) for k, v in self._llm.items()},
            }

    def prometheus_text(self) -> str:
        snap = self.snapshot()
        out = ["# Foxclaw metrics", f"foxclaw_uptime_seconds {snap['uptime_s']}"]
        for ep, s in snap["endpoints"].items():
            lbl = 'endpoint="%s"' % ep.replace('"', "'")
            out.append(f'foxclaw_requests_total{{{lbl}}} {s["count"]}')
            out.append(f'foxclaw_request_errors_total{{{lbl}}} {s["errors"]}')
            out.append(f'foxclaw_request_latency_p50_ms{{{lbl}}} {s["p50_ms"]}')
            out.append(f'foxclaw_request_latency_p95_ms{{{lbl}}} {s["p95_ms"]}')
        for p, s in snap["llm"].items():
            lbl = 'provider="%s"' % p.replace('"', "'")
            out.append(f'foxclaw_llm_calls_total{{{lbl}}} {s["count"]}')
            out.append(f'foxclaw_llm_errors_total{{{lbl}}} {s["errors"]}')
            out.append(f'foxclaw_llm_latency_p95_ms{{{lbl}}} {s["p95_ms"]}')
        return "\n".join(out) + "\n"


# 进程级单例:中间件 + router 都往这里写,端点从这里读。
METRICS = MetricsCollector()
