"""浏览器导航的 URL 准入策略。

背景:ActionExecutor 原本「只做翻译不做判断」,`navigate` 直接把模型给的字符串
喂给 driver → 可被用来打 `file://`(读本机文件)、`javascript:`(在页面上下文执行)、
`http://169.254.169.254`(云元数据)或内网地址(SSRF 探测)。

这一层只做**协议与地址**的准入判断(判断该不该做这个动作属 HAR 职责),默认
deny-by-default,允许内网时用 FOXCLAW_BROWSER_ALLOW_PRIVATE=1 显式放开。
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")


def _allow_private() -> bool:
    return os.environ.get("FOXCLAW_BROWSER_ALLOW_PRIVATE", "").strip() in ("1", "true", "yes")


def _is_internal_ip(ip) -> bool:
    return (ip.is_loopback or ip.is_private or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


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

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if _is_internal_ip(ip):
            raise ValueError(f"已拒绝内网/本机地址 {host}(如确需访问请设 "
                             f"FOXCLAW_BROWSER_ALLOW_PRIVATE=1)")
        return target

    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith(".localhost"):
        raise ValueError("已拒绝本机域名 localhost(SSRF 防护)")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return target                    # 解析不了 → 交给浏览器,失败也是页面错误
    for info in infos:
        addr = info[4][0]
        try:
            if _is_internal_ip(ipaddress.ip_address(addr)):
                raise ValueError(f"已拒绝域名 {host}(解析到内网地址 {addr})")
        except ValueError:
            continue
    return target
