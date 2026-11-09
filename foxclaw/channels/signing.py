"""渠道出站签名 / 入站验签(HMAC-SHA256,时间窗 + nonce 防重放)。

**为什么需要**:渠道消息目前是"明文 HTTPS + Bearer token"。TLS 只保护链路,保护不了
两端之间被中间人代理/日志重放的**消息语义**:网关收到一条 `{"conversation":…, "text":
"转账 5000"}` 无法判断它真是 Foxclaw 发的、还是别人照着抓包重放的。

**为什么只在「我们两端都控制」的方向签**:Telegram/Discord/飞书/微信的 API 不验我们的
签名 —— 给它们的请求加 `X-Foxclaw-Signature` 纯属表演(它们会忽略,攻击者也不需要
伪造,拿 bot token 直连即可)。真正有威胁的是 Foxclaw ↔ 自建网关(OpenClaw 桥)这条
链路:两端都是自己的代码,共享密钥可落地,签名才有验证方。

**签名结构**(canonical string,避免"把 header 挪来挪去"各种拼接口径不一致):

    v1 \n {timestamp} \n {nonce} \n {sha256(body) 的 hex}

- `body` 摘要进签名 → 改正文即失效(不只是改 header 才算篡改);
- `timestamp` 进签名 → 超出容差(默认 300s)直接拒,缩小重放窗口;
- `nonce` 进签名 + 验方 `ReplayGuard` 记账 → 窗口内也不能重放同一请求;
- `v1` 前缀 → 将来换算法可并存,不炸老客户端。

配置:`FOXCLAW_CHANNEL_SIGNING_SECRET`(共享密钥)、可选
`FOXCLAW_CHANNEL_SIGNING_KEY_ID`(轮换时对账用)。未配置 = 不签(opt-in);
一旦配置,签名失败就**不发**(fail-closed,绝不静默降级成明文)。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from typing import Mapping, Optional, Tuple

SIGN_VERSION = "v1"
SIGNATURE_HEADER = "X-Foxclaw-Signature"
TIMESTAMP_HEADER = "X-Foxclaw-Timestamp"
NONCE_HEADER = "X-Foxclaw-Nonce"
KEY_ID_HEADER = "X-Foxclaw-Key-Id"

ENV_SECRET = "FOXCLAW_CHANNEL_SIGNING_SECRET"
ENV_KEY_ID = "FOXCLAW_CHANNEL_SIGNING_KEY_ID"

DEFAULT_TOLERANCE = 300.0


def body_digest(body: bytes) -> str:
    """正文摘要(绑定进签名,防正文被改)。"""
    return hashlib.sha256(body or b"").hexdigest()


def canonical(ts: str, nonce: str, body: bytes) -> bytes:
    """签名原文。字段顺序/分隔符改动 = 破坏兼容,测试里锁死。"""
    return f"{SIGN_VERSION}\n{ts}\n{nonce}\n{body_digest(body)}".encode()


def sign(secret: str, ts: str, nonce: str, body: bytes) -> str:
    """计算签名值(hex)。"""
    return hmac.new((secret or "").encode("utf-8"),
                    canonical(ts, nonce, body), hashlib.sha256).hexdigest()


class ReplayGuard:
    """验方 nonce 记账:同一 nonce 在 TTL 内只接受一次。

    线程安全 + 有界(超过上限丢最旧),避免被刷爆内存:nonce 是攻击者可控输入,
    无界集合等于给了一个内存耗尽面。
    """

    def __init__(self, ttl: float = DEFAULT_TOLERANCE, max_entries: int = 4096):
        self.ttl = float(ttl)
        self.max_entries = max(16, int(max_entries))
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def purge(self, now: float | None = None) -> int:
        """清掉过期 nonce,返回清理条数。"""
        cut = (now if now is not None else time.time()) - self.ttl
        with self._lock:
            dead = [k for k, t in self._seen.items() if t < cut]
            for k in dead:
                self._seen.pop(k, None)
        return len(dead)

    def seen(self, nonce: str, now: float | None = None) -> bool:
        """见过 → True(**这是重放**);没见过则登记并返回 False。"""
        if not nonce:
            return True
        now = now if now is not None else time.time()
        with self._lock:
            cut = now - self.ttl
            dead = [k for k, t in self._seen.items() if t < cut]
            for k in dead:
                self._seen.pop(k, None)
            if nonce in self._seen:
                return True
            if len(self._seen) >= self.max_entries:
                oldest = min(self._seen.items(), key=lambda kv: kv[1])[0]
                self._seen.pop(oldest, None)
            self._seen[nonce] = now
            return False

    def __len__(self) -> int:
        return len(self._seen)


class OutboundSigner:
    """出站签名器:给一次请求生成 4 个 header。"""

    def __init__(self, secret: str, key_id: str = ""):
        self.secret = secret or ""
        self.key_id = key_id or ""

    @property
    def enabled(self) -> bool:
        return bool(self.secret)

    def headers(self, body: bytes, nonce: str = "", now: float | None = None) -> dict:
        """生成签名头。未配置密钥直接抛错(fail-closed:调用方必须先判 `enabled`)。"""
        if not self.enabled:
            raise ValueError("未配置渠道签名密钥(FOXCLAW_CHANNEL_SIGNING_SECRET)")
        ts = str(int(now if now is not None else time.time()))
        nonce = nonce or secrets.token_hex(12)
        h = {
            SIGNATURE_HEADER: f"{SIGN_VERSION}={sign(self.secret, ts, nonce, body)}",
            TIMESTAMP_HEADER: ts,
            NONCE_HEADER: nonce,
        }
        if self.key_id:
            h[KEY_ID_HEADER] = self.key_id
        return h


def _header(headers: Mapping, name: str) -> str:
    """大小写无关取 header(HTTP header 名不区分大小写,不同框架给的大小写不同)。"""
    if not headers:
        return ""
    if name in headers:
        return str(headers[name] or "")
    low = name.lower()
    for k, v in headers.items():
        if str(k).lower() == low:
            return str(v or "")
    return ""


def verify(headers: Mapping, body: bytes, secret: str,
           tolerance: float = DEFAULT_TOLERANCE, replay: ReplayGuard | None = None,
           now: float | None = None) -> Tuple[bool, str]:
    """验签。返回 (是否通过, 原因码)。

    原因码:``ok`` / ``missing_secret`` / ``missing_header`` / ``bad_version`` /
    ``expired`` / ``replay`` / ``bad_signature``(排错时能一眼看出是哪一层挂了)。
    ``replay`` 传 None 则**不做**防重放(只验真伪),由调用方决定是否记账。
    """
    if not secret:
        return False, "missing_secret"
    sig_raw = _header(headers, SIGNATURE_HEADER)
    ts = _header(headers, TIMESTAMP_HEADER)
    nonce = _header(headers, NONCE_HEADER)
    if not sig_raw or not ts or not nonce:
        return False, "missing_header"
    ver, _, sig = sig_raw.partition("=")
    if ver != SIGN_VERSION or not sig:
        return False, "bad_version"
    try:
        ts_f = float(ts)
    except (TypeError, ValueError):
        return False, "bad_version"
    now = now if now is not None else time.time()
    if abs(now - ts_f) > float(tolerance):
        return False, "expired"
    expect = sign(secret, ts, nonce, body)
    if not hmac.compare_digest(expect, sig):
        return False, "bad_signature"
    if replay is not None and replay.seen(nonce, now=now):
        return False, "replay"
    return True, "ok"


def from_env(env: Mapping | None = None) -> Optional[OutboundSigner]:
    """按环境变量构造签名器;未配置密钥 → None(不签,opt-in)。"""
    env = env if env is not None else os.environ
    secret = (env.get(ENV_SECRET) or "").strip()
    if not secret:
        return None
    return OutboundSigner(secret, key_id=(env.get(ENV_KEY_ID) or "").strip())
