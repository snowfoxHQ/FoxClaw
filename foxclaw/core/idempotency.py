"""
Foxclaw Core — 幂等守卫 (Idempotency Guard)

借鉴 OpenClaw 的幂等原理,适配 Foxclaw。核心思想:
  给每个"只应发生一次"的操作算一个稳定 key,执行前查重——
  记录过就跳过(返回上次结果),没记录才执行并落记录。

为什么需要:消息平台天生会重发(网络抖动、平台重试、轮询游标边界),
没有幂等,用户会收到重复回复、同一操作被记两次。

设计要点:
  · key 稳定:同一逻辑操作每次算出同一 key(版本+域+标识)
  · 跨重启:可选落盘,进程重启后仍能识别"这条处理过了"
  · 不膨胀:TTL 过期清理 + 容量上限(LRU 淘汰)
  · 并发安全:RLock 保护
"""
from __future__ import annotations
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field


@dataclass
class _Record:
    key: str
    created: float
    result: dict = field(default_factory=dict)   # 上次执行的结果(重发时原样返回)


def make_key(domain: str, *parts: str, version: str = "v1") -> str:
    """构造稳定幂等 key:version:domain:hash(parts)。

    domain 区分用途(message/dispatch/reply...),parts 是业务标识
    (渠道+消息ID、会话+步骤号等)。相同输入永远得到相同 key。
    """
    raw = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"{version}:{domain}:{digest}"


class IdempotencyGuard:
    """幂等守卫。check_and_mark 是核心:原子地"查重 + 标记"。

    【P2-3】持久化从"全量快照"改为"追加日志 + 定期 compact":
      · 每次操作(mark/check_and_mark/update_result/容量淘汰)只追加一行 JSONL(O(1) 写);
      · 每 compact_every 次操作(或启动加载后)compact 成快照并清空日志;
      · 启动时读快照 + 回放追加日志(兼容旧的纯快照格式:无日志可回放,自然兼容);
      · 持久化失败不再静默:计数进 stats(),有 spine 时发 idempotency.persist_failed 事件。
    """

    def __init__(self, ttl_seconds: float = 3600, max_size: int = 10000,
                 persist_path: str | None = None, spine=None,
                 compact_every: int = 1000):
        self.ttl = ttl_seconds
        self.max_size = max_size
        self.persist_path = persist_path
        self._store: dict[str, _Record] = {}
        self._lock = threading.RLock()
        # 【P2-3】审计脊柱(可空,None 时静默)
        self._spine = spine
        # 【P2-3】追加日志 + 定期 compact
        self._log_path = (persist_path + ".log") if persist_path else None
        self._compact_every = compact_every
        self._ops_since_compact = 0
        self._persist_failures = 0
        self._compact_lock = threading.Lock()
        if persist_path:
            self._load()

    def seen(self, key: str) -> bool:
        """该 key 是否已记录(且未过期)。"""
        with self._lock:
            self._evict_expired()
            return key in self._store

    def get_result(self, key: str) -> dict | None:
        """取上次执行结果(用于重发时原样返回,避免重复执行副作用)。"""
        with self._lock:
            rec = self._store.get(key)
            return dict(rec.result) if rec else None

    def mark(self, key: str, result: dict | None = None):
        """标记 key 已执行,记录结果。"""
        with self._lock:
            self._store[key] = _Record(key=key, created=time.time(),
                                       result=dict(result or {}))
            self._enforce_capacity()
            self._persist_op("mark", key, created=self._store[key].created,
                             result=self._store[key].result)
            need_compact = self._ops_since_compact >= self._compact_every
        if need_compact:
            self._try_compact()     # 全量写盘放主锁外(见 _try_compact)

    def check_and_mark(self, key: str, result: dict | None = None) -> bool:
        """原子操作:若 key 未见过 → 标记并返回 True(应执行);
        若已见过 → 返回 False(应跳过)。

        这是防并发重复的关键:查重和标记在同一把锁里完成,
        避免"两个重发同时查到未记录、都去执行"。
        """
        with self._lock:
            self._evict_expired()
            if key in self._store:
                return False        # 已处理过,跳过
            self._store[key] = _Record(key=key, created=time.time(),
                                       result=dict(result or {}))
            self._enforce_capacity()
            self._persist_op("mark", key, created=self._store[key].created,
                             result=self._store[key].result)
            need_compact = self._ops_since_compact >= self._compact_every
        if need_compact:
            self._try_compact()
        return True

    def update_result(self, key: str, result: dict):
        """补写结果(check_and_mark 先占位、执行完再回填结果时用)。"""
        with self._lock:
            rec = self._store.get(key)
            if rec:
                rec.result = dict(result or {})
                self._persist_op("update", key, result=rec.result)
                need_compact = self._ops_since_compact >= self._compact_every
            else:
                need_compact = False
        if need_compact:
            self._try_compact()

    # ── 内部:过期/容量/持久化 ──────────────────────────────
    def _evict_expired(self):
        if self.ttl <= 0:
            return
        now = time.time()
        expired = [k for k, r in self._store.items() if now - r.created > self.ttl]
        for k in expired:
            del self._store[k]

    def _enforce_capacity(self):
        # 超容量 → 淘汰最旧的(按 created)。
        # 【P2-3】淘汰必须落 delete 日志:否则重启回放追加日志时,被淘汰的 key
        # 会"复活"(mark 日志还在,没有对应的删除记录)。锁内调用。
        if len(self._store) <= self.max_size:
            return
        items = sorted(self._store.items(), key=lambda kv: kv[1].created)
        for k, _ in items[: len(self._store) - self.max_size]:
            del self._store[k]
            self._persist_op("delete", k)

    # ── 内部:持久化(追加日志 + 定期 compact)─────────────────
    def _persist_op(self, event: str, key: str, **fields):
        """【P2-3】追加一条日志(O(1) 写,不写全量)。锁内调用(append 很快,
        且在锁内可避免并发写同一文件交错)。失败计数 + 发事件,不影响主流程。"""
        if not self._log_path:
            return
        try:
            entry = {"event": event, "key": key, "ts": time.time(), **fields}
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._ops_since_compact += 1
        except Exception:
            self._persist_failures += 1
            self._emit_persist_failed()

    def _emit_persist_failed(self):
        """【P2-3】持久化失败发事件(有 spine 时),失败本身绝不再抛。"""
        if self._spine is None:
            return
        try:
            self._spine.publish("idempotency.persist_failed", {
                "failures": self._persist_failures,
                "log_path": self._log_path,
            }, source="idempotency")
        except Exception:
            pass

    def _try_compact(self):
        """【P2-3】触发 compact,**全量写盘在主锁外**:
        主锁内只做两件快事——日志轮转(rename,新 append 自动开新文件)和内存快照拷贝;
        慢的全量 JSON 写盘在锁外做,不阻塞并发 mark/check。
        独立 _compact_lock 非阻塞抢占:抢不到说明另一线程正在 compact,直接跳过。
        正确性:轮转后新 append 进新 .log,写快照+删轮转本不影响它们,无丢失窗口。"""
        if not self.persist_path:
            return
        if not self._compact_lock.acquire(blocking=False):
            return
        try:
            with self._lock:
                rotated = None
                if self._log_path and os.path.exists(self._log_path):
                    rotated = self._log_path + ".rot"
                    os.replace(self._log_path, rotated)
                now = time.time()
                snapshot = {k: {"created": r.created, "result": r.result}
                            for k, r in self._store.items()
                            if self.ttl <= 0 or now - r.created <= self.ttl}
                self._ops_since_compact = 0
            # ── 以下为主锁外的慢 IO ──
            tmp = self.persist_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False)
            os.replace(tmp, self.persist_path)   # 原子替换,防写一半损坏
            if rotated and os.path.exists(rotated):
                os.remove(rotated)
        except Exception:
            with self._lock:
                self._persist_failures += 1
                self._emit_persist_failed()
        finally:
            self._compact_lock.release()

    def _compact(self):
        """启动加载后的同步 compact(单线程上下文,直接全量写)。"""
        if not self.persist_path:
            return
        try:
            now = time.time()
            data = {k: {"created": r.created, "result": r.result}
                    for k, r in self._store.items()
                    if self.ttl <= 0 or now - r.created <= self.ttl}
            tmp = self.persist_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.persist_path)   # 原子替换,防写一半损坏
            if self._log_path and os.path.exists(self._log_path):
                os.remove(self._log_path)
            self._ops_since_compact = 0
        except Exception:
            self._persist_failures += 1
            self._emit_persist_failed()

    def _apply_log_event(self, ev: dict, now: float):
        """回放一条追加日志事件。"""
        kind = ev.get("event")
        key = ev.get("key")
        if not key:
            return
        if kind == "mark":
            created = ev.get("created", now)
            if self.ttl <= 0 or now - created <= self.ttl:
                self._store[key] = _Record(key=key, created=created,
                                           result=ev.get("result", {}))
        elif kind == "update":
            rec = self._store.get(key)
            if rec:
                rec.result = ev.get("result", {})
        elif kind == "delete":
            self._store.pop(key, None)

    def _load(self):
        """读全量快照 + 回放追加日志。兼容旧的纯快照格式(无日志,回放为空)。
        仅当真有日志被回放时才 compact 一次(合并进快照)——否则每次构造都白写
        一份全量快照,失去"追加日志 O(1)"的意义。"""
        try:
            now = time.time()
            if os.path.exists(self.persist_path):
                with open(self.persist_path, encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.items():
                    created = v.get("created", now)
                    if self.ttl <= 0 or now - created <= self.ttl:
                        self._store[k] = _Record(key=k, created=created,
                                                 result=v.get("result", {}))
            replayed = False
            if self._log_path and os.path.exists(self._log_path):
                with open(self._log_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except Exception:
                            continue    # 跳过损坏行(append 末行可能写一半)
                        self._apply_log_event(ev, now)
                        replayed = True
            if replayed:
                self._compact()
        except Exception:
            pass        # 加载失败 → 从空开始,不影响启动

    def stats(self) -> dict:
        with self._lock:
            self._evict_expired()
            return {"size": len(self._store), "max_size": self.max_size,
                    "ttl_seconds": self.ttl, "persisted": bool(self.persist_path),
                    "persist_failures": self._persist_failures,      # 【P2-3】
                    "ops_since_compact": self._ops_since_compact}    # 【P2-3】
