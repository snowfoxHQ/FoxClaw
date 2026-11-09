"""
统一 API 网关限流(gateway.py)

把散落在各处(MCP、/chat/stream)的单点限流收敛成一层**统一网关**:
  · per-client 令牌桶——突发限流(每分钟额度,允许瞬时打满桶)
  · 固定窗口配额——持续限流(每小时总额度)
  · 空闲客户端淘汰——防攻击者轮换 key/IP 把 per-client 字典撑爆内存(与 metrics「unmatched」同款教训)

client 身份 = X-API-Key(有则)否则来源 IP。线程安全(单锁,check 内无 await/无阻塞 I/O)。
默认宽松:本地单用户近乎无感;对外暴露时经环境变量收紧。
"""
from __future__ import annotations

import time
import threading


class Gateway:
    def __init__(self, rate_per_min: int = 600, quota_per_hour: int = 20000,
                 max_clients: int = 4096):
        self.rate = max(1, int(rate_per_min))
        self.capacity = float(self.rate)
        self.refill = self.capacity / 60.0
        self.quota = max(1, int(quota_per_hour))
        self.max_clients = max(64, int(max_clients))
        self._lock = threading.Lock()
        self._buckets: dict = {}
        self._quota: dict = {}
        self._seen: dict = {}
        self.rejected = 0

    def check(self, client: str):
        """判定一次请求。返回 (allowed: bool, retry_after: int, reason: str)。
        reason ∈ {'', 'rate', 'quota'}。allowed=False 时 retry_after 为建议重试秒数。"""
        now = time.time()
        with self._lock:
            self._evict_locked(now)
            self._seen[client] = now

            q = self._quota.get(client)
            if q is None or now - q[1] >= 3600.0:
                q = [0, now]
                self._quota[client] = q
            if q[0] >= self.quota:
                self.rejected += 1
                return False, int(3600.0 - (now - q[1])) + 1, "quota"

            b = self._buckets.get(client)
            if b is None:
                b = [self.capacity, now]
                self._buckets[client] = b
            b[0] = min(self.capacity, b[0] + (now - b[1]) * self.refill)
            b[1] = now
            if b[0] < 1.0:
                self.rejected += 1
                return False, int((1.0 - b[0]) / self.refill) + 1, "rate"

            b[0] -= 1.0
            q[0] += 1
            return True, 0, ""

    def _evict_locked(self, now: float):
        """超过 max_clients 时淘汰最久未见的一批(降到 ~90%)。必须在锁内调用。"""
        if len(self._seen) <= self.max_clients:
            return
        n_drop = len(self._seen) - self.max_clients + max(1, self.max_clients // 10)
        victims = sorted(self._seen.items(), key=lambda kv: kv[1])[:n_drop]
        for c, _ in victims:
            self._seen.pop(c, None)
            self._buckets.pop(c, None)
            self._quota.pop(c, None)

    def stats(self) -> dict:
        with self._lock:
            return {
                "tracked_clients": len(self._seen),
                "rate_per_min": self.rate,
                "quota_per_hour": self.quota,
                "max_clients": self.max_clients,
                "rejected_total": self.rejected,
            }
