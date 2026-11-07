"""安装审核门:技能注册前的治理审核(对齐 OpenClaw security.installPolicy)。
非杀毒——远程 URL 无法扫描;做 SSRF 预检/域名黑名单/明文告警/副作用与信任提示,
产出 allow|warn|deny,风险项要求用户显式确认。"""
from __future__ import annotations
import os
import ipaddress
import socket
from urllib.parse import urlparse


def _host_deny() -> set:
    return {h.strip().lower() for h in os.environ.get("FOXCLAW_SKILL_HOST_DENY", "").split(",") if h.strip()}


def _is_internal_host(host: str) -> bool:
    if not host:
        return True
    if host.lower() == "localhost":
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False   # 解析不了 → 不在安装期硬挡,交运行时 SSRF 兜底
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            return True
        if str(addr) == "169.254.169.254":   # 云元数据
            return True
    return False


def review_install(spec) -> dict:
    """返回 {verdict: allow|warn|deny, reasons: [...], warnings: [...]}。"""
    reasons, warnings = [], []
    if spec.kind == "web_api":
        host = (urlparse(spec.endpoint).hostname or "").lower()
        if host in _host_deny():
            reasons.append(f"域名 {host} 在黑名单")
        if not spec.allow_local and _is_internal_host(host):
            reasons.append(f"endpoint 指向内网/本地/元数据地址({host or '?'}),已拦截(SSRF)")
        if spec.endpoint.startswith("http://"):
            warnings.append("使用明文 HTTP,传输不加密")
    if spec.kind == "mcp":
        warnings.append("MCP 来源为外部低信任,其返回内容不应被当作指令执行")
    if spec.side_effect in ("write", "send", "pay") or spec.destructive:
        warnings.append(f"该技能有副作用({spec.side_effect}{'/不可逆' if spec.destructive else ''}),会真实影响外部")
    if reasons:
        return {"verdict": "deny", "reasons": reasons, "warnings": warnings}
    if warnings:
        return {"verdict": "warn", "reasons": [], "warnings": warnings}
    return {"verdict": "allow", "reasons": [], "warnings": []}


def review_mcp_server(url: str) -> dict:
    """MCP server 接入审核。外部低信任 → 恒 warn(需确认);内网/元数据/黑名单 → deny。"""
    from urllib.parse import urlparse
    reasons, warnings = [], []
    host = (urlparse(url).hostname or "").lower()
    if host in _host_deny():
        reasons.append(f"域名 {host} 在黑名单")
    if _is_internal_host(host):
        reasons.append(f"MCP url 指向内网/本地/元数据地址({host or '?'}),已拦截(SSRF)")
    if url.startswith("http://"):
        warnings.append("使用明文 HTTP,传输不加密")
    warnings.append("接入外部 MCP server,其返回内容为外部不可信数据,不应被当作指令执行")
    if reasons:
        return {"verdict": "deny", "reasons": reasons, "warnings": warnings}
    return {"verdict": "warn", "reasons": [], "warnings": warnings}   # 外部低信任恒 warn
