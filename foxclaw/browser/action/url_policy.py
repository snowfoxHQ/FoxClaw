"""浏览器导航与请求的 URL 准入策略。

背景:ActionExecutor 原本「只做翻译不做判断」,`navigate` 直接把模型给的字符串
喂给 driver → 可被用来打 `file://`(读本机文件)、`javascript:`(在页面上下文执行)、
`http://169.254.169.254`(云元数据)或内网地址(SSRF 探测)。

两层,缺一不可:
- `validate_navigate_url` —— 模型显式要求的导航目标(动作层)。
- `validate_request_url`  —— 页面上**任何**网络请求:3xx 跳转后的新地址、iframe、
  子资源(img/script/fetch)。只校验导航目标时,一个公网页面跳一次转、或塞一个
  `<img src="http://169.254.169.254/latest/meta-data/">` 就绕过去了。

默认 deny-by-default,允许内网时用 FOXCLAW_BROWSER_ALLOW_PRIVATE=1 显式放开。

【残余风险 · DNS TOCTOU】本层校验时解析一次域名,浏览器之后会**自己再解析一次**。
DNS rebinding 可以让第一次返回公网、第二次返回 127.0.0.1。彻底消除得在启动参数里
把域名钉成 IP(Chromium `--host-resolver-rules`)或下沉到 CDP 层 —— Playwright 的
route 回调拿不到「浏览器将要连接哪个 IP」。本层把风险从「整个会话不校验」收窄到
「每次请求校验一次」,不声称消除。
"""

from __future__ import annotations

import ipaddress
import os
import socket
import threading
import time
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")

INTERNAL_SCHEMES = ("data", "blob", "about")

_RESOLVE_TTL = 60.0
_MAX_CACHE = 512
_resolve_cache: dict[str, tuple[bool, float]] = {}
_resolve_lock = threading.Lock()


def _allow_private() -> bool:
    return os.environ.get("FOXCLAW_BROWSER_ALLOW_PRIVATE", "").strip() in ("1", "true", "yes")


def _is_internal_ip(ip) -> bool:
    return (ip.is_loopback or ip.is_private or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _resolve_internal(host: str) -> bool:
    """域名是否解析到内网地址。解析失败按「未知」处理 → False(交给浏览器失败)。

    不缓存失败结果:离线/瞬时 DNS 故障后恢复,没必要让页面一分钟内都起不来。
    """
    now = time.monotonic()
    with _resolve_lock:
        hit = _resolve_cache.get(host)
        if hit is not None and hit[1] > now:
            return hit[0]
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    internal = False
    for info in infos:
        addr = str(info[4][0]).split("%", 1)[0]
        try:
            parsed = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_internal_ip(parsed):
            internal = True
            break
    with _resolve_lock:
        if len(_resolve_cache) >= _MAX_CACHE:
            _resolve_cache.clear()
        _resolve_cache[host] = (internal, now + _RESOLVE_TTL)
    return internal


def _deny_reason(host: str) -> str | None:
    """host 命中本机/内网 → 返回拒绝原因;合法公网主机 → None。"""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if _is_internal_ip(ip):
            return (f"已拒绝内网/本机地址 {host}(如确需访问请设 "
                    f"FOXCLAW_BROWSER_ALLOW_PRIVATE=1)")
        return None

    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith(".localhost"):
        return "已拒绝本机域名 localhost(SSRF 防护)"
    if _resolve_internal(host):
        return f"已拒绝域名 {host}(解析到内网地址)"
    return None


def validate_navigate_url(url: str, allow_private: bool | None = None) -> str:
    """校验并规范化导航目标;不合法抛 ValueError(调用方转成用户可读错误)。

    允许:http/https + 公网主机。
    拒绝:其它协议(file/javascript/data/blob…)、无主机、以及(默认)回环/私网/
    链路本地/保留地址。域名会尝试解析一次以拦「域名指向内网」;解析失败不拦
    (离线场景下页面本来也打不开,不该因此拒掉合法 URL)。
    """
    target = (url or "").strip()
    if not target:
        raise ValueError("导航目标为空")
    parsed = urlparse(target)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"只允许 http/https 导航,已拒绝 {scheme or '(无协议)'}://")
    host = (parsed.hostname or "").strip()
    if not host:
        raise ValueError("导航 URL 缺少主机名")
    if allow_private is None:
        allow_private = _allow_private()
    if allow_private:
        return target

    reason = _deny_reason(host)
    if reason:
        raise ValueError(reason)
    return target


def validate_request_url(url: str, allow_private: bool | None = None) -> str:
    """单个网络请求(子资源 / 跳转 / iframe)的准入。

    与 `validate_navigate_url` 的唯一差别是豁免页面内部 scheme
    (`data:` / `blob:` / `about:`):它们由页面自身产生、不经过网络栈。
    其余规则完全一致 —— 跳转后的地址、iframe、img/script 都要过这一关。
    """
    target = (url or "").strip()
    if not target:
        raise ValueError("请求 URL 为空")
    scheme = (urlparse(target).scheme or "").lower()
    if scheme in INTERNAL_SCHEMES:
        return target
    return validate_navigate_url(target, allow_private)
