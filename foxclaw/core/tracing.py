"""
请求链路追踪(tracing.py)

与既有可观测设施的分工(不重复造):
  METRICS      聚合视角:各端点/LLM 的计数、错误率、延迟分位 —— 回答"整体健康吗"
  ThoughtChain 单 Agent 的推理节点 —— 回答"它当时怎么想的"
  EventSpine   事件流(correlation_id 逐事件随机)—— 回答"发生过什么"
  Trace(这里) 单次请求视角:一个 trace_id 贯穿 agent→LLM→skill→memory,带层级 span
               与耗时 —— 回答"**这一次**请求慢在哪、调了谁"

实现要点:
  · contextvars 传播:与 _suppress_memory 同款。asyncio.to_thread / asyncio.run 会带上
    上下文,故后台线程里的 span 仍归属发起它的请求。
  · span() 在无 trace 时是**零成本 no-op**(不在请求里跑的后台任务不会报错/不产生垃圾)。
  · 有界环形存储:只留最近 N 条 trace,防内存无限增长(与 metrics「unmatched」同款教训)。
  · 线程安全:Trace.spans 与 TraceStore 各自加锁。
"""
from __future__ import annotations

import os
import json
import time
import uuid
import random
import sqlite3
import logging
import threading
import contextvars
from collections import deque
from contextlib import contextmanager
from . import settings

logger = logging.getLogger(__name__)


# ── 采样策略 ────────────────────────────────────────────────
# 内存环存只留最近 N 条、重启即失 → 查不了"昨天那次慢请求"。持久化解决它,但不能无脑全存:
# 正常请求的 trace 数量大、价值低。故:**慢的 / 出错的必存**(那才是你要查的),
# 其余按采样率(默认 0 —— 个人 OS 留下的每条都值得看)。
def _slow_ms() -> float:
    return float(settings.get_safe("FOXCLAW_TRACE_SLOW_MS"))


def _sample_rate() -> float:
    return max(0.0, min(1.0, float(settings.get_safe("FOXCLAW_TRACE_SAMPLE"))))


def _retain() -> int:
    return max(100, int(settings.get_safe("FOXCLAW_TRACE_RETAIN")))

_current: contextvars.ContextVar = contextvars.ContextVar("foxclaw_trace", default=None)

MAX_SPANS_PER_TRACE = 200          # 单条 trace 的 span 上限,防病态循环把内存撑爆


# ── W3C Trace Context(零依赖跨进程传播)────────────────────────
# 为什么不上 opentelemetry-sdk:Foxclaw 是单进程 + local-first,为它装 SDK/exporter/grpc、
# 再要求跑个 Jaeger,代价远大于收益。但它确实有一条真实跨进程边界——**MCP 双向**。
# traceparent 是 W3C 标准、纯字符串、零依赖,恰好覆盖这条边界:
#   00-{trace_id:32hex}-{span_id:16hex}-{flags:2hex}
_TP_RE = __import__("re").compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


def parse_traceparent(header: str):
    """解析入站 traceparent,返回 trace_id(32hex)或 None。非法/缺失一律 None(不信外部输入)。"""
    if not header:
        return None
    m = _TP_RE.match(str(header).strip().lower())
    if not m:
        return None
    tid = m.group(1)
    if tid == "0" * 32:                       # 全零非法(规范要求)
        return None
    return tid


def to_traceparent(trace_id: str, span_id: str | None = None) -> str:
    """给出站请求生成 traceparent。span_id 缺省随机(本进程作为对端的 parent)。"""
    tid = (trace_id or uuid.uuid4().hex)[:32].rjust(32, "0")
    sid = (span_id or uuid.uuid4().hex[:16])[:16].rjust(16, "0")
    return f"00-{tid}-{sid}-01"


def current_traceparent():
    """当前上下文的 traceparent(无 trace 则 None)——出站调用带上它即可跨进程接链。"""
    tr = _current.get()
    return to_traceparent(tr.trace_id) if tr is not None else None


class Trace:
    def __init__(self, name: str, trace_id: str | None = None):
        # 32 hex:直接符合 W3C traceparent 的 trace-id 规格(跨进程可对齐)
        self.trace_id = trace_id or uuid.uuid4().hex
        self.name = name
        self.start_ts = time.time()
        self._t0 = time.perf_counter()
        self.duration_ms = None
        self.spans: list = []
        self._lock = threading.Lock()
        self._dropped = 0

    def add_span(self, span: dict):
        with self._lock:
            if len(self.spans) >= MAX_SPANS_PER_TRACE:
                self._dropped += 1
                return
            self.spans.append(span)

    def finish(self):
        self.duration_ms = (time.perf_counter() - self._t0) * 1000.0

    def has_error(self) -> bool:
        with self._lock:
            return any(not s.get("ok", True) for s in self.spans)

    def to_dict(self, with_spans: bool = True) -> dict:
        with self._lock:
            spans = list(self.spans)
        d = {
            "trace_id": self.trace_id,
            "name": self.name,
            "start_ts": self.start_ts,
            "duration_ms": round(self.duration_ms, 2) if self.duration_ms is not None else None,
            "span_count": len(spans),
            "dropped_spans": self._dropped,
        }
        if with_spans:
            d["spans"] = spans
        return d


class TraceStore:
    """近期用有界环存(快),值得留的落 SQLite(可查历史)。"""

    def __init__(self, limit: int = 200):
        self._d: deque = deque(maxlen=limit)
        self._lock = threading.Lock()
        self._db_path = None

    # ── 持久化 ───────────────────────────────────────────
    def attach_db(self, path):
        """开启持久化并建表。连接**即建即弃、不跨线程复用**(与 ThoughtChain 同款):
        SQLite 连接不可跨线程共享,而 trace 写入发生在请求线程/to_thread 工作线程。"""
        self._db_path = str(path)
        try:
            with self._conn() as c:
                c.execute("""CREATE TABLE IF NOT EXISTS traces(
                    trace_id TEXT PRIMARY KEY, name TEXT, start_ts REAL,
                    duration_ms REAL, span_count INTEGER, ok INTEGER, spans TEXT)""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_tr_ts ON traces(start_ts)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_tr_dur ON traces(duration_ms)")
        except Exception as e:
            logger.warning("trace 持久化不可用(降级为仅内存): %s", e)
            self._db_path = None

    def _conn(self):
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")   # 单写多读,降锁竞争
        except sqlite3.OperationalError:
            pass
        return conn

    def _should_persist(self, tr: Trace) -> bool:
        if tr.has_error():
            return True                                   # 出错的必留
        if (tr.duration_ms or 0) >= _slow_ms():
            return True                                   # 慢的必留(正是要查的)
        return random.random() < _sample_rate()           # 其余按采样率(默认 0)

    def _persist(self, tr: Trace):
        d = tr.to_dict()
        try:
            with self._conn() as c:
                c.execute("INSERT OR REPLACE INTO traces VALUES(?,?,?,?,?,?,?)",
                          (tr.trace_id, tr.name, tr.start_ts, tr.duration_ms,
                           d["span_count"], 0 if tr.has_error() else 1,
                           json.dumps(d["spans"], ensure_ascii=False)))
                # 保留上限:超出删最旧 → 有界增长(不会把磁盘吃光)
                c.execute("DELETE FROM traces WHERE trace_id IN ("
                          "SELECT trace_id FROM traces ORDER BY start_ts DESC "
                          "LIMIT -1 OFFSET ?)", (_retain(),))
        except Exception as e:
            logger.warning("trace 落库失败(不影响请求): %s", e)

    def put(self, tr: Trace):
        with self._lock:
            self._d.append(tr)
        if self._db_path and self._should_persist(tr):
            self._persist(tr)                             # 锁外落库(慢 I/O 不挡内存环)
        try:
            OTLP.submit(tr)                               # 可选导出:未配 endpoint 即 no-op;
        except Exception:                                 # 队列满则丢,绝不阻塞请求
            pass

    def get(self, trace_id: str):
        with self._lock:
            for tr in reversed(self._d):
                if tr.trace_id == trace_id:
                    return tr
        return None

    def get_dict(self, trace_id: str):
        """先查内存环,未命中再查库(重启后仍能捞出历史慢请求)。"""
        tr = self.get(trace_id)
        if tr is not None:
            return tr.to_dict()
        if not self._db_path:
            return None
        try:
            with self._conn() as c:
                row = c.execute("SELECT trace_id,name,start_ts,duration_ms,span_count,spans "
                                "FROM traces WHERE trace_id=?", (trace_id,)).fetchone()
        except Exception:
            return None
        if not row:
            return None
        return {"trace_id": row[0], "name": row[1], "start_ts": row[2],
                "duration_ms": row[3], "span_count": row[4],
                "spans": json.loads(row[5] or "[]"), "persisted": True}

    def query(self, limit: int = 30, slow_ms: float | None = None, errors_only: bool = False):
        """查已落库的历史 trace(内存环之外)。这才是"找出昨天那次慢请求"的入口。"""
        if not self._db_path:
            return []
        sql = "SELECT trace_id,name,start_ts,duration_ms,span_count,ok FROM traces"
        cond, args = [], []
        if slow_ms is not None:
            cond.append("duration_ms >= ?"); args.append(float(slow_ms))
        if errors_only:
            cond.append("ok = 0")
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY start_ts DESC LIMIT ?"; args.append(int(limit))
        try:
            with self._conn() as c:
                rows = c.execute(sql, args).fetchall()
        except Exception:
            return []
        return [{"trace_id": r[0], "name": r[1], "start_ts": r[2], "duration_ms": r[3],
                 "span_count": r[4], "ok": bool(r[5]), "persisted": True} for r in rows]

    def list(self, limit: int = 30) -> list:
        with self._lock:
            items = list(self._d)[-limit:]
        return [t.to_dict(with_spans=False) for t in reversed(items)]

    def clear(self):
        with self._lock:
            self._d.clear()


# ── OTLP/HTTP-JSON 导出(可选、零依赖)────────────────────────
# 想接 Jaeger/Tempo/collector 的人设 FOXCLAW_OTLP_ENDPOINT 即可;不设则完全不启用、零成本。
# 刻意**不引 opentelemetry-sdk**:OTLP 有 HTTP+JSON 传输,stdlib urllib 就能发。
# 铁律:遥测**绝不能拖慢请求** —— 后台守护线程 + 有界队列,满了就丢(宁丢遥测不拖请求)。
def _otlp_endpoint() -> str:
    return os.environ.get("FOXCLAW_OTLP_ENDPOINT", "").strip().rstrip("/")


def _otlp_service() -> str:
    return os.environ.get("FOXCLAW_OTLP_SERVICE", "foxclaw").strip()


def _attrs(d: dict) -> list:
    return [{"key": str(k), "value": {"stringValue": str(v)}} for k, v in (d or {}).items()]


def _trace_to_otlp(tr: "Trace") -> dict:
    """把一条 Trace 转成 OTLP/JSON。根 span 代表整个请求,其余作为其子 span。"""
    t_ns = lambda sec: str(int(sec * 1_000_000_000))
    root_id = uuid.uuid4().hex[:16]
    dur = (tr.duration_ms or 0.0) / 1000.0
    spans = [{
        "traceId": tr.trace_id, "spanId": root_id, "name": tr.name, "kind": 2,   # SERVER
        "startTimeUnixNano": t_ns(tr.start_ts),
        "endTimeUnixNano": t_ns(tr.start_ts + dur),
        "status": {"code": 2 if tr.has_error() else 1},
    }]
    for s in tr.to_dict()["spans"]:
        st = s.get("ts", tr.start_ts)                       # 缺 ts(手工 add_span)→ 兜底
        spans.append({
            "traceId": tr.trace_id, "spanId": uuid.uuid4().hex[:16], "parentSpanId": root_id,
            "name": s.get("name", "span"), "kind": 1,       # INTERNAL
            "startTimeUnixNano": t_ns(st),
            "endTimeUnixNano": t_ns(st + (s.get("ms", 0.0) / 1000.0)),
            "attributes": _attrs(s.get("meta")),
            "status": {"code": 1 if s.get("ok", True) else 2},
        })
    return {"resourceSpans": [{
        "resource": {"attributes": _attrs({"service.name": _otlp_service()})},
        "scopeSpans": [{"scope": {"name": "foxclaw.tracing"}, "spans": spans}],
    }]}


class _OtlpExporter:
    """后台导出:有界队列 + 守护线程。队列满即丢(计数可见),永不阻塞调用方。"""

    def __init__(self, maxsize: int = 512):
        import queue
        self._q = queue.Queue(maxsize=maxsize)
        self._th = None
        self._lock = threading.Lock()
        self.dropped = 0
        self.sent = 0
        self.failed = 0

    def _ensure_worker(self):
        with self._lock:
            if self._th is None or not self._th.is_alive():
                self._th = threading.Thread(target=self._run, name="otlp-export", daemon=True)
                self._th.start()

    def submit(self, tr: "Trace"):
        import queue
        if not _otlp_endpoint():
            return
        self._ensure_worker()
        try:
            self._q.put_nowait(tr)
        except queue.Full:
            self.dropped += 1        # 宁可丢遥测,也不让导出拖住请求线程

    def _run(self):
        import urllib.request
        while True:
            tr = self._q.get()
            ep = _otlp_endpoint()
            if not ep:
                continue
            try:
                body = json.dumps(_trace_to_otlp(tr)).encode("utf-8")
                req = urllib.request.Request(
                    ep + "/v1/traces", data=body, method="POST",
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    r.read()
                self.sent += 1
            except Exception as e:
                self.failed += 1
                if self.failed <= 3:      # 限量告警,别把日志刷爆
                    logger.warning("OTLP 导出失败(不影响请求): %s", e)

    def stats(self) -> dict:
        return {"enabled": bool(_otlp_endpoint()), "endpoint": _otlp_endpoint(),
                "sent": self.sent, "failed": self.failed, "dropped": self.dropped,
                "queued": self._q.qsize()}


OTLP = _OtlpExporter()
TRACES = TraceStore()


def start_trace(name: str, trace_id: str | None = None) -> Trace:
    """开一条 trace 并绑到当前上下文(由 API 中间件按请求调用)。"""
    tr = Trace(name, trace_id)
    _current.set(tr)
    return tr


def current_trace():
    return _current.get()


def clear_trace():
    _current.set(None)


@contextmanager
def span(name: str, **meta):
    """记录一个 span(嵌套用 parent 串联)。不在 trace 里 → 零成本 no-op。
    异常也会被记进 span(ok=False + error),再原样抛出(不吞异常)。"""
    tr = _current.get()
    if tr is None:
        yield None
        return
    t0 = time.perf_counter()
    # ts:span 开始的 epoch 秒。内部只需 ms 就够,但 OTLP 要求每个 span 有 start/end 绝对时刻,
    # 故一并记下(开销可忽略;缺失时导出侧会优雅兜底)。
    rec = {"name": name, "ts": time.time(), "ok": True,
           **({"meta": meta} if meta else {})}
    try:
        yield rec
    except Exception as e:
        rec["ok"] = False
        rec["error"] = f"{type(e).__name__}: {e}"[:200]
        raise
    finally:
        rec["ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        tr.add_span(rec)
