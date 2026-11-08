"""Sentinel —— Foxclaw 的独立守门人进程(Muse「Harness/Sentinel 分离」的本地最小版)。

背景:foxclaw 的动作裁决(skills.permission.evaluate)+ 统一门(kernel.policy_gate)此前都是
**主 Agent 进程内的软件模块**。好处是零开销、易测;代价是:一旦 Agent 进程被 RCE,裁决逻辑、
规则表、以及散落在 os.environ 里的真实凭证会一起暴露 —— 那是「用提示词约束模型」的残留,
不是「用系统结构约束 Agent」。

本模块把两件事挪出主进程,放进一个**独立进程**:
  1. evaluate —— 动作裁决(纯函数,跨进程执行);
  2. mint/redeem —— 凭证代注入(真实凭证只存在于守门人进程,主进程只拿一次性可撤销令牌,真值永不出进程);
  3. mcp_register/mcp_call —— MCP 代执行(守门人持有 MCP server 配置与凭证,主进程只发意图)。

跨平台:stdio JSON-lines(Windows 无 unix socket)。父进程 spawn 本进程经管道通信。
默认关闭:不设 FOXCLAW_GUARDIAN=1 时,foxclaw 完全走原进程内路径,行为逐字不变。

独立启动(部署到常开机时也可这样常驻): python -m foxclaw.kernel.sentinel
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path


# ── 凭证保险箱(vault)────────────────────────────────────────
# 真实凭证只进守门人进程,主进程内存里从不出现。两个来源:
#   1. 环境变量前缀 FOXCLAW_GUARDIAN_SECRET_<REF>(REF 统一小写);
#   2. FOXCLAW_GUARDIAN_SECRETS 指向的 JSON 文件 {"ref": "secret", ...}。
def load_vault() -> dict:
    vault: dict = {}
    prefix = "FOXCLAW_GUARDIAN_SECRET_"
    for k, v in os.environ.items():
        if k.startswith(prefix) and v:
            vault[k[len(prefix):].lower()] = v
    path = os.environ.get("FOXCLAW_GUARDIAN_SECRETS", "").strip()
    if path and Path(path).exists():
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
            if isinstance(d, dict):
                vault.update({str(k).lower(): v for k, v in d.items()})
        except Exception:
            pass  # 损坏的 secrets 文件不拖垮守门人;宁可少借不错借
    return vault


# ── MCP server 配置(守门人持有凭证)──────────────────────────
# 与 vault 对称:MCP 的 headers 里可能含真凭证(Authorization/x-api-key 等)。
# 主进程通过 mcp_register 推送配置;也可用 FOXCLAW_GUARDIAN_MCP_SERVERS 指向 JSON 文件做初始加载。
def _mcp_servers_path() -> str:
    """守门人侧 MCP 配置持久化文件。优先 FOXCLAW_GUARDIAN_MCP_SERVERS 显式指定;
    否则用 FOXCLAW_GUARDIAN_DATA_DIR 下的 mcp_guardian_servers.json。主进程的 mcp_servers.json 已不含 headers。"""
    p = os.environ.get("FOXCLAW_GUARDIAN_MCP_SERVERS", "").strip()
    if p:
        return p
    d = os.environ.get("FOXCLAW_GUARDIAN_DATA_DIR", "").strip()
    if d:
        return str(Path(d) / "mcp_guardian_servers.json")
    return ""


def load_mcp_servers() -> dict:
    servers: dict = {}
    path = _mcp_servers_path()
    if not path or not Path(path).exists():
        return servers
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        for c in d.get("servers", []) or []:
            sid = str(c.get("id", "")).strip()
            if not sid:
                continue
            servers[sid] = {
                "name": c.get("name") or sid,
                "url": c.get("url", ""),
                "headers": dict(c.get("headers") or {}),
                "transport": c.get("transport") or "streamable_http",
            }
    except Exception:
        pass  # 损坏配置不拖垮守门人;宁可少配不错配
    return servers


def _persist_mcp_servers(servers: dict) -> None:
    path = _mcp_servers_path()
    if not path:
        return
    try:
        out = {"servers": [dict(v) for v in servers.values()]}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(path).with_suffix(".tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(Path(path))
    except Exception:
        pass  # 持久化失败不拖垮守门人;重启后主进程会重新 mcp_register


# ── 短期可撤销能力令牌 ──────────────────────────────────────
# 彻底取代「resolve 真值借出」:主进程只拿得到一个不透明随机 token,真值永不出守门人进程。
# 令牌 = 一次性 + 过期 + 可撤销,进程内状态表(不依赖 HMAC 签名,因为 token 本身高熵、无法伪造/猜中)。
class TokenStore:
    def __init__(self, default_ttl: float = 60.0):
        self._default_ttl = default_ttl
        self._tokens: dict = {}

    def mint(self, ref: str, ttl: float = None) -> dict:
        """为某 ref 签发能力令牌。返回 {token, expires_in},绝不返回真值。"""
        ttl = ttl if ttl is not None else self._default_ttl
        token = secrets.token_urlsafe(32)
        self._tokens[token] = {
            "ref": ref,
            "expires_at": time.monotonic() + ttl,
            "used": False,
            "revoked": False,
        }
        return {"token": token, "expires_in": ttl}

    def redeem(self, token: str):
        """一次性兑换:返回绑定的 ref 并立即作废。无效/过期/已用/已撤销 → None。"""
        rec = self._tokens.get(token or "")
        if rec is None or rec["revoked"] or rec["used"]:
            return None
        if time.monotonic() > rec["expires_at"]:
            self._tokens.pop(token, None)
            return None
        rec["used"] = True
        return rec["ref"]

    def revoke(self, token: str) -> bool:
        """主动撤销。返回是否真的撤销了一个活跃令牌。"""
        rec = self._tokens.get(token or "")
        if rec is None or rec["used"] or rec["revoked"]:
            return False
        rec["revoked"] = True
        return True


# ── 跨进程重建(守门人侧)─────────────────────────────────────
def _rebuild_skill(sd: dict):
    """主进程只传 evaluate 需要的字段 + 已算好的 trust。重建为鸭子类型对象。"""
    from types import SimpleNamespace
    trust = sd.get("trust", "low")
    return SimpleNamespace(
        id=sd.get("id", ""),
        kind=sd.get("kind", "local"),
        side_effect=sd.get("side_effect", "write"),     # 未声明 → 有副作用
        destructive=bool(sd.get("destructive", False)),
        external=bool(sd.get("external", False)),
        read_only=sd.get("read_only", None),
        params=sd.get("params", {}) or {},
        trust_level=lambda trust=trust: trust,
    )


def _rebuild_rules(rd: dict):
    from foxclaw.skills.permission import PermissionRules
    return PermissionRules(
        deny=list(rd.get("deny") or []),
        ask=list(rd.get("ask") or []),
        allow=list(rd.get("allow") or []),
    )


def _handle_evaluate(msg: dict) -> dict:
    from foxclaw.skills.permission import evaluate
    skill = _rebuild_skill(msg.get("skill") or {})
    rules = _rebuild_rules(msg.get("rules") or {})
    d = evaluate(
        skill, msg.get("args") or {}, rules=rules,
        confirmed=bool(msg.get("confirmed")),
        mode=msg.get("mode", "strict"),
        taint_level=int(msg.get("taint_level", 0)),
        taint_threshold=int(msg.get("taint_threshold", 0)),
    )
    return d.to_dict()


def _handle_mint(msg: dict, vault: dict, store: TokenStore) -> dict:
    ref = str(msg.get("ref", "")).lower()
    if not ref:
        return {"error": "missing ref"}
    if ref not in vault:
        return {"error": f"unknown secret ref: {ref}"}
    ttl = msg.get("ttl")
    ttl = float(ttl) if ttl else None
    out = store.mint(ref, ttl)
    print(f"[Sentinel] mint {ref}", file=sys.stderr, flush=True)
    return out  # {"token", "expires_in"} —— 不含真值


def _handle_revoke(msg: dict, store: TokenStore) -> dict:
    token = str(msg.get("token", "") or "")
    if not token:
        return {"error": "missing token"}
    ok = store.revoke(token)
    print(f"[Sentinel] revoke {'ok' if ok else 'miss'}", file=sys.stderr, flush=True)
    return {"revoked": ok}


def _handle_mcp_register(msg: dict, servers: dict) -> dict:
    """主进程推送 MCP server 配置给守门人,全量同步。headers 非空则覆盖、为空则保留旧值:
    第4步起主进程不长期持有 headers(日常只推元数据),仅在 add_server 那一次临时带 headers。
    凭证只在守门人进程内,主进程之后只发意图(server_id + tool + args)。"""
    items = msg.get("servers") or []
    new_servers: dict = {}
    for c in items:
        sid = str(c.get("id", "")).strip()
        if not sid:
            continue
        headers = dict(c.get("headers") or {})
        old = servers.get(sid)
        if not headers and old:
            headers = dict(old.get("headers") or {})  # 主进程不持 headers,不覆盖守门人已有凭证
        new_servers[sid] = {
            "name": c.get("name") or sid,
            "url": c.get("url", ""),
            "headers": headers,
            "transport": c.get("transport") or "streamable_http",
        }
    servers.clear()
    servers.update(new_servers)
    _persist_mcp_servers(servers)
    print(f"[Sentinel] mcp_register {len(servers)} servers", file=sys.stderr, flush=True)
    return {"count": len(servers)}


def _handle_mcp_call(msg: dict, servers: dict) -> dict:
    """mcp 代执行:守门人进程持有 MCP server 配置(含凭证),在进程内调用 MCP,返回 SkillResult.to_dict()。
    主进程只传意图(server_id + tool + args),拿不到凭证、不直连外部。"""
    from foxclaw.skills import SkillResult, _mark_untrusted

    skill_id = msg.get("skill_id", "mcp")
    server_id = str(msg.get("server_id", "") or "")
    tool = str(msg.get("tool", "") or "")
    args = dict(msg.get("args") or {})

    if not server_id or not tool:
        return SkillResult(skill_id, False, status="not_connected",
                           message="未配置 MCP server/tool").to_dict()
    cfg = servers.get(server_id)
    if cfg is None:
        return SkillResult(skill_id, False, status="not_connected",
                           message=f"MCP server「{server_id}」未在守门人注册").to_dict()

    try:
        from foxclaw.skills.mcp_client import _get_bridge, _amcp_call_tool
        out = _get_bridge().run(_amcp_call_tool(
            cfg["url"], dict(cfg.get("headers") or {}), cfg["transport"], tool, args))
        if out.get("isError"):
            return SkillResult(skill_id, False, status="blocked",
                               message=f"MCP 工具返回错误: {out.get('content') or 'unknown'}").to_dict()
        out = _mark_untrusted(out)   # 外部 MCP 返回=不可信,防提示注入
        print(f"[Sentinel] mcp_call {server_id}.{tool}", file=sys.stderr, flush=True)
        return SkillResult(skill_id, True, output=out,
                           message="MCP 调用完成(外部不可信数据)").to_dict()
    except Exception as e:
        return SkillResult(skill_id, False, status="blocked",
                           message=f"MCP 调用失败: {e}").to_dict()


def _serialize_tool(tool) -> dict:
    """把 mcp.types.Tool 序列化为纯 dict(跨进程 JSON 可传)。annotations 决定 side_effect/destructive。"""
    ann = getattr(tool, "annotations", None)
    return {
        "name": getattr(tool, "name", "") or "",
        "description": getattr(tool, "description", "") or "",
        "inputSchema": getattr(tool, "inputSchema", {}) or {},
        "annotations": {
            "readOnlyHint": getattr(ann, "readOnlyHint", None) if ann else None,
            "destructiveHint": getattr(ann, "destructiveHint", None) if ann else None,
        },
    }


def _handle_mcp_list_tools(msg: dict, servers: dict) -> dict:
    """发现阶段代执行:守门人用自己持有的 headers 连外部 MCP 拉工具列表,序列化为 dict 列表返回。
    主进程不碰 headers、不直连外部;工具注解(side_effect/destructive)也由守门人侧真实返回。"""
    server_id = str(msg.get("server_id", "") or "")
    cfg = servers.get(server_id)
    if cfg is None:
        return {"error": f"MCP server「{server_id}」未在守门人注册"}
    try:
        from foxclaw.skills.mcp_client import _get_bridge, _amcp_list_tools
        tools = _get_bridge().run(_amcp_list_tools(
            cfg["url"], dict(cfg.get("headers") or {}), cfg["transport"]))
        out = [_serialize_tool(t) for t in tools]
        print(f"[Sentinel] mcp_list_tools {server_id} -> {len(out)} tools", file=sys.stderr, flush=True)
        return {"tools": out}
    except Exception as e:
        return {"error": f"list_tools 失败: {e}"}


def _inject_secret(headers: dict, args: dict, auth_scheme: str, secret: str) -> None:
    """按 auth_scheme 把真凭证注入请求(只在守门人进程内,真值不出进程)。
    bearer / header:<name> / query:<name>;空或未知 → 不注入(调用方未声明注入方式)。"""
    scheme = (auth_scheme or "").strip()
    if scheme == "bearer":
        headers["Authorization"] = f"Bearer {secret}"
    elif scheme.startswith("header:"):
        headers[scheme[len("header:"):]] = secret
    elif scheme.startswith("query:"):
        args[scheme[len("query:"):]] = secret


def _handle_execute_web(msg: dict, vault: dict, store: TokenStore) -> dict:
    """web_api 代执行:守门人进程注入真凭证后发 HTTP,返回 SkillResult.to_dict()。
    凭证来源优先级:一次性能力令牌(token,先 redeem) > 直接 ref(兼容第 1 步)。
    真值只在守门人进程内从 vault 取,永不出进程。"""
    from foxclaw.skills import SkillResult
    from foxclaw.skills import ssrf_check

    skill_id = msg.get("skill_id", "web_api")
    endpoint = msg.get("endpoint", "")
    method = (msg.get("method") or "GET").upper()
    token = str(msg.get("token", "") or "")
    secret_ref = str(msg.get("secret_ref", "")).lower()
    auth_scheme = msg.get("auth_scheme", "bearer") or "bearer"
    args = dict(msg.get("args") or {})
    loc = msg.get("param_location", "auto")
    timeout = float(msg.get("timeout", 10.0))
    allow_local = bool(msg.get("allow_local", False))

    if not endpoint:
        return SkillResult(skill_id, False, status="not_connected",
                           message="未配置 endpoint").to_dict()

    secret = None
    used_ref = None
    if token:
        ref = store.redeem(token)   # 一次性:兑换即作废
        if ref is None:
            return SkillResult(skill_id, False, status="blocked",
                               message="能力令牌无效/已过期/已使用/已撤销").to_dict()
        secret = vault.get(ref)
        used_ref = ref
        if secret is None:
            return SkillResult(skill_id, False, status="blocked",
                               message=f"令牌绑定的凭证 ref「{ref}」已不在 vault 中").to_dict()
    elif secret_ref:
        secret = vault.get(secret_ref)
        used_ref = secret_ref
        if secret is None:
            return SkillResult(skill_id, False, status="blocked",
                               message=f"凭证 ref「{secret_ref}」未在守门人 vault 中配置").to_dict()

    # SSRF 校验:守门人侧也做(主进程可能被攻破传内网地址,不能只信主进程)
    block = ssrf_check(endpoint, allow_local=allow_local)
    if block:
        return SkillResult(skill_id, False, status="blocked",
                           message=f"端点被安全策略拒绝: {block}").to_dict()

    headers = {"User-Agent": "Foxclaw/1.0"}
    if secret is not None:
        _inject_secret(headers, args, auth_scheme, secret)

    if loc == "auto":
        loc = "query" if method in ("GET", "DELETE") else "json"

    try:
        import httpx
    except ImportError:
        return SkillResult(skill_id, False, status="blocked",
                           message="缺少 httpx 依赖,无法发起 Web API 调用").to_dict()

    req_kwargs = {"headers": headers, "timeout": timeout, "follow_redirects": False}
    if loc == "query":
        req_kwargs["params"] = args
    else:
        req_kwargs["json"] = args

    try:
        with httpx.Client() as client:
            resp = client.request(method, endpoint, **req_kwargs)
    except httpx.TimeoutException:
        return SkillResult(skill_id, False, status="timeout",
                           message=f"Web API 调用超时(>{timeout}s)").to_dict()
    except httpx.RequestError as e:
        return SkillResult(skill_id, False, status="blocked",
                           message=f"Web API 请求失败: {e}").to_dict()

    if 300 <= resp.status_code < 400:
        return SkillResult(skill_id, False, status="blocked",
                           message=f"Web API 返回重定向 HTTP {resp.status_code}(已阻止跟随)").to_dict()
    if resp.status_code >= 400:
        return SkillResult(skill_id, False, status="blocked",
                           message=f"Web API 返回 HTTP {resp.status_code}",
                           output={"status_code": resp.status_code, "body": resp.text[:500]}).to_dict()
    try:
        data = resp.json()
    except Exception:
        data = {"text": resp.text[:2000]}
    print(f"[Sentinel] execute_web {method} {endpoint} ref={used_ref or '-'}", file=sys.stderr, flush=True)
    return SkillResult(skill_id, True, status="done",
                       output={"status_code": resp.status_code, "data": data},
                       message=f"Web API 调用成功(HTTP {resp.status_code})").to_dict()


def _reply(resp: dict) -> None:
    sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def serve_stdio(vault: dict, store: TokenStore, servers: dict) -> None:
    """JSON-lines 循环:stdin 读一行请求 → stdout 回一行响应。父进程退出(stdin 关闭)时自然结束。"""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            _reply({"id": None, "ok": False, "error": "bad json"})
            continue
        mid = msg.get("id")
        op = msg.get("op")
        try:
            if op == "ping":
                result = {"pong": True}
            elif op == "evaluate":
                result = _handle_evaluate(msg)
            elif op == "mint":
                result = _handle_mint(msg, vault, store)
            elif op == "revoke":
                result = _handle_revoke(msg, store)
            elif op == "mcp_register":
                result = _handle_mcp_register(msg, servers)
            elif op == "mcp_call":
                result = _handle_mcp_call(msg, servers)
            elif op == "mcp_list_tools":
                result = _handle_mcp_list_tools(msg, servers)
            elif op == "execute_web":
                result = _handle_execute_web(msg, vault, store)
            elif op == "shutdown":
                _reply({"id": mid, "ok": True, "result": {"bye": True}})
                return
            else:
                result = {"error": f"unknown op: {op}"}
            _reply({"id": mid, "ok": True, "result": result})
        except Exception as e:  # 单条请求失败不影响守门人存活
            _reply({"id": mid, "ok": False, "error": str(e)})


def main(argv=None) -> None:
    vault = load_vault()
    store = TokenStore()
    servers = load_mcp_servers()
    serve_stdio(vault, store, servers)


if __name__ == "__main__":
    main()
