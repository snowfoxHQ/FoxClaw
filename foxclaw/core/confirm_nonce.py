"""关键操作的会话 nonce 二次确认(Confirmation Broker)。

**问题**:send / pay / destructive 这类动作由权限管线挡成 `ask`(需确认),但"确认"本身
只是一个布尔——`run_task(..., _confirmed=True)` 或 `POST /api/ahs/run {confirmed:true}`
就能放行。它没有绑定**确认的是哪一件事**:昨天确认过"给 A 发周报",今天同一个
`confirmed=true` 会连"给 B 转账"一起放行;调用方也可以无脑重发同一个 true 反复执行。

**解法**:确认变成一次性凭据——
  · `issue(session, action, args)` 签发 nonce,绑定 `会话 + 动作 + 参数指纹 + 有效期`;
  · 执行前 `consume(...)` 校验并**立即作废**(单次使用),参数变了指纹就对不上;
  · 过期即废(默认 300s),超窗重放无效。

**边界(诚实)**:nonce 只证明"调用方看到过这次待确认",不证明"是人类按的"。真正的
人在回路仍需渠道/UI 把人接进环路;本模块解决的是"确认对象漂移"和"确认可重放"。

线程安全 + 有界:nonce 来自外部,无界集合等于开一个内存耗尽面。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

CRITICAL_SIDE_EFFECTS = ("send", "pay")

DEFAULT_TTL = 300.0


def action_fingerprint(action: str, args: dict) -> str:
    """动作指纹:同名 + 同参(字典序无关)= 同一件事。

    参数进指纹是关键:确认"给 A 发"之后把 args 改成 B,指纹不匹配 → 拒绝执行,
    堵掉「确认了这件事、执行了另一件」。
    """
    try:
        payload = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        payload = repr(args)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{action}#{digest}"


def is_critical(side_effect: str, destructive: bool = False) -> bool:
    """该动作是否必须走 nonce 二次确认。"""
    return bool(destructive) or (side_effect or "") in CRITICAL_SIDE_EFFECTS


@dataclass
class PendingConfirm:
    """一条待确认记录。"""
    nonce: str
    action: str
    fingerprint: str
    session: str = ""
    issued: float = field(default_factory=time.time)
    expires: float = 0.0
    used: bool = False

    def to_dict(self) -> dict:
        return {"nonce": self.nonce, "action": self.action,
                "session": self.session, "issued": self.issued,
                "expires_at": self.expires, "used": self.used}


class ConfirmationBroker:
    """签发 / 校验一次性确认码。"""

    def __init__(self, ttl: float = DEFAULT_TTL, max_pending: int = 256,
                 clock=time.time):
        self.ttl = float(ttl)
        self.max_pending = max(8, int(max_pending))
        self._clock = clock
        self._items: dict[str, PendingConfirm] = {}
        self._lock = threading.Lock()

    def issue(self, session: str, action: str, args: dict,
              ttl: float | None = None) -> dict:
        """签发一个绑定到 (会话, 动作, 参数) 的一次性确认码。"""
        now = self._clock()
        ttl = self.ttl if ttl is None else float(ttl)
        item = PendingConfirm(
            nonce=secrets.token_hex(8),
            action=action or "",
            fingerprint=action_fingerprint(action or "", args or {}),
            session=session or "",
            issued=now, expires=now + ttl)
        with self._lock:
            self._purge_locked(now)
            if len(self._items) >= self.max_pending:
                oldest = min(self._items.values(), key=lambda it: it.issued)
                self._items.pop(oldest.nonce, None)
            self._items[item.nonce] = item
        return {"nonce": item.nonce, "action": item.action,
                "expires_at": item.expires, "ttl": ttl}

    def consume(self, session: str, nonce: str, action: str = "",
                args: dict | None = None, now: float | None = None) -> Tuple[bool, str]:
        """校验确认码并**立即作废**。返回 (是否通过, 原因码)。

        原因码:``ok`` / ``missing_nonce`` / ``unknown_nonce`` / ``already_used`` /
        ``expired`` / ``session_mismatch`` / ``action_mismatch`` / ``args_mismatch``。
        会话为空(任一侧)时不校验会话——便于进程内调用;HTTP 入口务必带 session。
        """
        if not nonce:
            return False, "missing_nonce"
        now = now if now is not None else self._clock()
        with self._lock:
            item: Optional[PendingConfirm] = self._items.get(nonce)
            if item is not None and item.expires <= now:
                self._items.pop(nonce, None)
                return False, "expired"
            self._purge_locked(now)
            if item is None:
                return False, "unknown_nonce"
            if item.used:
                return False, "already_used"
            if item.session and session and item.session != session:
                return False, "session_mismatch"
            if action and item.action and item.action != action:
                return False, "action_mismatch"
            if args is not None and item.fingerprint != action_fingerprint(item.action, args):
                return False, "args_mismatch"
            item.used = True
            self._items.pop(nonce, None)
            return True, "ok"

    def pending(self, session: str = "") -> list:
        """未消费且未过期的确认码(可按会话过滤),供排查/前端展示。"""
        now = self._clock()
        with self._lock:
            self._purge_locked(now)
            return [it.to_dict() for it in self._items.values()
                    if not it.used and (not session or it.session == session)]

    def purge(self, now: float | None = None) -> int:
        now = now if now is not None else self._clock()
        with self._lock:
            return self._purge_locked(now)

    def _purge_locked(self, now: float) -> int:
        dead = [k for k, it in self._items.items() if it.expires <= now]
        for k in dead:
            self._items.pop(k, None)
        return len(dead)

    def __len__(self) -> int:
        return len(self._items)
