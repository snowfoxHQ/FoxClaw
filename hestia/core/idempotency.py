"""
Hestia Core — 幂等守卫 (Idempotency Guard)

借鉴 OpenClaw 的幂等原理,适配 Hestia。核心思想:
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
    """幂等守卫。check_and_mark 是核心:原子地"查重 + 标记"。"""

    def __init__(self, ttl_seconds: float = 3600, max_size: int = 10000,
                 persist_path: str = None):
        self.ttl = ttl_seconds
        self.max_size = max_size
        self.persist_path = persist_path
        self._store: dict[str, _Record] = {}
        self._lock = threading.RLock()
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

    def mark(self, key: str, result: dict = None):
        """标记 key 已执行,记录结果。"""
        with self._lock:
            self._store[key] = _Record(key=key, created=time.time(),
                                       result=dict(result or {}))
            self._enforce_capacity()
            self._persist()

    def check_and_mark(self, key: str, result: dict = None) -> bool:
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
            self._persist()
            return True

    def update_result(self, key: str, result: dict):
        """补写结果(check_and_mark 先占位、执行完再回填结果时用)。"""
        with self._lock:
            rec = self._store.get(key)
            if rec:
                rec.result = dict(result or {})
                self._persist()

    # ── 内部:过期/容量/持久化 ──────────────────────────────
    def _evict_expired(self):
        if self.ttl <= 0:
            return
        now = time.time()
        expired = [k for k, r in self._store.items() if now - r.created > self.ttl]
        for k in expired:
            del self._store[k]

    def _enforce_capacity(self):
        # 超容量 → 淘汰最旧的(按 created)
        if len(self._store) <= self.max_size:
            return
        items = sorted(self._store.items(), key=lambda kv: kv[1].created)
        for k, _ in items[: len(self._store) - self.max_size]:
            del self._store[k]

    def _persist(self):
        if not self.persist_path:
            return
        try:
            data = {k: {"created": r.created, "result": r.result}
                    for k, r in self._store.items()}
            tmp = self.persist_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.persist_path)   # 原子替换,防写一半损坏
        except Exception:
            pass        # 持久化失败不影响主流程(内存仍生效)

    def _load(self):
        try:
            if not os.path.exists(self.persist_path):
                return
            with open(self.persist_path, encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            for k, v in data.items():
                created = v.get("created", now)
                if self.ttl <= 0 or now - created <= self.ttl:
                    self._store[k] = _Record(key=k, created=created,
                                             result=v.get("result", {}))
        except Exception:
            pass        # 加载失败 → 从空开始,不影响启动

    def stats(self) -> dict:
        with self._lock:
            self._evict_expired()
            return {"size": len(self._store), "max_size": self.max_size,
                    "ttl_seconds": self.ttl, "persisted": bool(self.persist_path)}
