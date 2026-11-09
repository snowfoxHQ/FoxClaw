"""AHS 执行链:技能安装/管理、MCP 接入、Markdown 指令技能、策略封杀、运行、断路器、钩子审计。

从 foxclaw/api.py 原样搬移(纯搬移,零行为变更)。共享 OS 实例经 `_api.current_os()`
晚绑定读取;路由用 `@app` 装饰器在 import 时注册。
"""

import asyncio
import uuid
from typing import Optional, cast

from fastapi import HTTPException
from pydantic import BaseModel

import foxclaw.api as _api
from .. import app


# ============ AHS 执行链(Agent Harness Skills 标准)============

class AHSRunPayload(BaseModel):
    task: str
    confirmed: bool = False
    # 【P2-2】断点续跑:传上次响应里的 pending_confirmation(确认后连 confirmed=True 一起传)。
    # Optional:pydantic v2 下 dict=None 字段收到显式 null 会 422,必须 Optional。
    resume_state: Optional[dict] = None
    # 【P1】关键操作(send/pay/destructive)二次确认码:上次响应 pending_confirmation.confirm_nonce。
    # 单次使用、绑定 skill+参数、默认 5 分钟过期。也可直接放在 resume_state 里,二者等价。
    confirm_nonce: str = ""


@app.get("/api/ahs/skills")
async def ahs_list_skills():
    """列出注册的 skill(含信任分级 trust + 封杀标记 blocked)。"""
    pol = _api.os_instance().skill_policy
    out = _api.os_instance().skill_registry.catalog()
    for s in out:
        s["blocked"] = pol.is_blocked(s.get("id", ""))
    return {"skills": out}


class PolicyDenyPayload(BaseModel):
    id: str


@app.get("/api/ahs/policy")
async def ahs_policy_get():
    """全局封杀策略快照(deny/allow)。"""
    return _api.os_instance().skill_policy.snapshot()


@app.post("/api/ahs/policy/deny")
async def ahs_policy_deny(p: PolicyDenyPayload):
    """封杀一个技能 id(deny 压倒 enable,立即对所有执行/注入生效)。"""
    _api.os_instance().skill_policy.deny(p.id)
    return {"ok": True, "policy": _api.os_instance().skill_policy.snapshot()}


@app.delete("/api/ahs/policy/deny/{skill_id}")
async def ahs_policy_undeny(skill_id: str):
    """解封。E1:被 policy 层(管理员 FOXCLAW_POLICY_DENY / policy.json)锁定的技能运行时无法解封,
    诚实返回 ok=False + 原因(不再假报成功);其余情况与改前一致(ok=True)。"""
    _api.os_instance().skill_policy.undeny(skill_id)
    snap = _api.os_instance().skill_policy.snapshot()
    if skill_id in snap.get("policy_deny", []):
        return {"ok": False,
                "reason": f"「{skill_id}」被管理员策略(policy 层)锁定,运行时无法解封", "policy": snap}
    return {"ok": True, "policy": snap}


class SkillInstallPayload(BaseModel):
    id: str
    name: str
    description: str = ""
    kind: str                                  # 只允许 "web_api" | "mcp"
    triggers: list = []
    side_effect: str = "write"                 # read/write/send/pay;不传按有副作用(会要求确认)
    destructive: bool = False
    external: bool = True
    params: dict = {}
    # web_api
    endpoint: str = ""
    http_method: str = "GET"
    http_headers: dict = {}
    param_location: str = "auto"
    allow_local: bool = False
    timeout: float = 10.0
    # mcp
    mcp_server: str = ""
    mcp_tool: str = ""
    acknowledged: bool = False                 # warn 级风险,用户确认后置 true


class SkillPatchPayload(BaseModel):
    enabled: bool


class SkillTestPayload(BaseModel):
    args: dict = {}
    confirmed: bool = False
    mode: str = "strict"
    # 关键操作(send/pay/destructive)的二次确认码:上次响应里的 confirm_nonce。
    # 宿主注入了 confirms(见 foxclaw/__init__.py)时,confirmed=True 只有配有效 nonce
    # 才放行 —— 原先这个端点收不到 nonce,界面上点「确认」永远退回 needs_confirmation。
    confirm_nonce: str = ""


_SKILL_KINDS = {"web_api", "mcp"}
_SIDE_EFFECTS = {"read", "write", "send", "pay"}


def _validate_install(p: "SkillInstallPayload"):
    if p.kind not in _SKILL_KINDS:
        raise HTTPException(400, "kind 只能是 web_api 或 mcp(不支持从界面安装本地代码技能)")
    if p.side_effect not in _SIDE_EFFECTS:
        raise HTTPException(400, "side_effect 非法")
    if p.kind == "web_api":
        if not (p.endpoint.startswith("http://") or p.endpoint.startswith("https://")):
            raise HTTPException(400, "web_api 需合法 http(s) endpoint")
    if p.kind == "mcp" and not (p.mcp_server and p.mcp_tool):
        raise HTTPException(400, "mcp 需同时提供 mcp_server 与 mcp_tool")


@app.post("/api/ahs/skills")
async def ahs_install_skill(p: SkillInstallPayload):
    """安装/注册一个 web_api|mcp 技能(过安装审核门 + 持久化)。同 id 覆盖更新。"""
    _validate_install(p)
    from foxclaw.skills import SideEffect, SkillKind, SkillSpec
    from foxclaw.skills.review import review_install
    reg = _api.os_instance().skill_registry
    _existing = reg.get(p.id)
    if _existing is not None and _existing.source == "builtin":
        raise HTTPException(409, "该 id 属于内置技能,不能覆盖")
    existed = _existing is not None
    spec = SkillSpec(
        id=p.id, name=p.name, description=p.description,
        kind=cast(SkillKind, p.kind), triggers=list(p.triggers),
        side_effect=cast(SideEffect, p.side_effect),
        destructive=p.destructive, external=p.external, params=dict(p.params),
        endpoint=p.endpoint, http_method=p.http_method, http_headers=dict(p.http_headers),
        param_location=p.param_location, allow_local=p.allow_local, timeout=p.timeout,
        mcp_server=p.mcp_server, mcp_tool=p.mcp_tool, source="user",
    )
    verdict = review_install(spec)                       # 安装审核门
    if verdict["verdict"] == "deny":
        raise HTTPException(403, detail={"error": "install_denied", "reasons": verdict["reasons"]})
    if verdict["verdict"] == "warn" and not p.acknowledged:
        raise HTTPException(409, detail={"needs_ack": True, "warnings": verdict["warnings"]})
    reg.register(spec)
    _api.os_instance().skill_store.save(reg)
    return {"ok": True, "skill": spec.to_dict(), "created": not existed, "warnings": verdict["warnings"]}


@app.patch("/api/ahs/skills/{skill_id}")
async def ahs_patch_skill(skill_id: str, p: SkillPatchPayload):
    """启停一个技能(内置/用户均可,持久化)。"""
    reg = _api.os_instance().skill_registry
    sp = reg.get(skill_id)
    if sp is None:
        raise HTTPException(404, "技能不存在")
    sp.enabled = bool(p.enabled)
    _api.os_instance().skill_store.save(reg)
    return {"ok": True, "skill": sp.to_dict()}


@app.delete("/api/ahs/skills/{skill_id}")
async def ahs_delete_skill(skill_id: str):
    """删除用户安装的技能;内置技能不可删(只能停用)。"""
    reg = _api.os_instance().skill_registry
    sp = reg.get(skill_id)
    if sp is None:
        raise HTTPException(404, "技能不存在")
    if sp.source == "builtin":
        raise HTTPException(409, "内置技能不可删除,只能停用")
    reg.unregister(skill_id)
    _api.os_instance().skill_store.save(reg)
    return {"ok": True}


@app.post("/api/ahs/skills/{skill_id}/test")
async def ahs_test_skill(skill_id: str, p: SkillTestPayload):
    """单技能测试调用:走真实权限管线/断路器/SSRF。副作用未确认 → needs_confirmation。"""
    reg = _api.os_instance().skill_registry
    sp = reg.get(skill_id)
    if sp is None:
        raise HTTPException(404, "技能不存在")
    # 阻塞 I/O(web_api httpx / mcp 桥,最长数十秒)放线程池,勿堵死事件循环(否则慢工具冻全站)
    res = await asyncio.to_thread(_api.os_instance().skill_adapter.invoke,
                                  sp, p.args, p.confirmed, p.mode,
                                  confirm_nonce=p.confirm_nonce)
    return res.to_dict()


class McpServerPayload(BaseModel):
    id: str
    name: str = ""
    url: str
    headers: dict = {}
    transport: str = "streamable_http"     # streamable_http | sse
    acknowledged: bool = False


@app.post("/api/ahs/mcp/servers")
async def mcp_add_server(p: McpServerPayload):
    """接入一个远程 MCP server:审核→连接拉工具→批量注册 mcp 技能→持久化。"""
    from foxclaw.skills.review import review_mcp_server
    from foxclaw.skills.mcp_client import tool_to_spec
    if p.transport not in ("streamable_http", "sse"):
        raise HTTPException(400, "transport 只能是 streamable_http 或 sse")
    if not (p.url.startswith("http://") or p.url.startswith("https://")):
        raise HTTPException(400, "url 需为 http(s)")
    verdict = review_mcp_server(p.url)
    if verdict["verdict"] == "deny":
        raise HTTPException(403, detail={"error": "install_denied", "reasons": verdict["reasons"]})
    if verdict["verdict"] == "warn" and not p.acknowledged:
        raise HTTPException(409, detail={"needs_ack": True, "warnings": verdict["warnings"]})
    cfg = {"id": p.id, "name": p.name or p.id, "url": p.url,
           "headers": dict(p.headers), "transport": p.transport}
    sentinel = getattr(_api.current_os(), "sentinel", None)
    try:
        if sentinel is not None:
            # 守门人模式:headers 只在这次调用经手,发现由守门人代执行,主进程只存元数据。
            tools = await asyncio.to_thread(_api.os_instance().mcp_add_server_guardian, cfg)
        else:
            tools = await asyncio.to_thread(_api.os_instance().mcp_manager.add_server, cfg)  # 桥阻塞→线程池
            _api.os_instance().mcp_store.save(_api.os_instance().mcp_manager.servers())
    except Exception as e:
        raise HTTPException(502, f"连接 MCP server 失败: {e}")
    reg = _api.os_instance().skill_registry
    n = 0
    for tool in tools:
        reg.register(tool_to_spec(p.id, tool)); n += 1
    _api.os_instance().skill_store.save(reg)
    _api.os_instance().sync_mcp_servers_to_sentinel()   # 元数据同步;守门人已持有 headers
    return {"ok": True, "server": {k: cfg[k] for k in ("id", "name", "url", "transport")},
            "tools_registered": n, "warnings": verdict["warnings"]}


@app.get("/api/ahs/mcp/servers")
async def mcp_list_servers():
    """列出已接入 MCP server(工具数由注册表派生,无需连接)。"""
    reg = _api.os_instance().skill_registry
    all_sk = reg.all_including_disabled()
    out = []
    for c in _api.os_instance().mcp_manager.servers():
        tc = sum(1 for s in all_sk if s.kind == "mcp" and s.mcp_server == c["id"])
        out.append({"id": c["id"], "name": c["name"], "url": c["url"],
                    "transport": c["transport"], "tool_count": tc})
    return {"servers": out}


@app.delete("/api/ahs/mcp/servers/{server_id}")
async def mcp_remove_server(server_id: str):
    """移除 server + 其注册的所有 mcp 技能。"""
    mgr = _api.os_instance().mcp_manager
    if not mgr.remove_server(server_id):
        raise HTTPException(404, "server 不存在")
    reg = _api.os_instance().skill_registry
    removed = [s.id for s in reg.all_including_disabled()
               if s.kind == "mcp" and s.mcp_server == server_id]
    for sid in removed:
        reg.unregister(sid)
    _api.os_instance().skill_store.save(reg)
    _api.os_instance().mcp_store.save(mgr.servers())
    _api.os_instance().sync_mcp_servers_to_sentinel()   # 配置(含凭证)同步给守门人
    return {"ok": True, "removed_skills": len(removed)}


@app.post("/api/ahs/mcp/servers/{server_id}/refresh")
async def mcp_refresh_server(server_id: str):
    """重连拉工具:新增/更新技能,删除已消失的工具技能。"""
    from foxclaw.skills.mcp_client import tool_to_spec
    mgr = _api.os_instance().mcp_manager
    if not any(s["id"] == server_id for s in mgr.servers()):
        raise HTTPException(404, "server 不存在")
    sentinel = getattr(_api.current_os(), "sentinel", None)
    try:
        if sentinel is not None:
            tools = await asyncio.to_thread(sentinel.mcp_list_tools, server_id)  # 守门人代发现(失败抛异常)
        else:
            tools = await asyncio.to_thread(mgr.list_tools, server_id)  # 桥阻塞→线程池
    except Exception as e:
        raise HTTPException(502, f"刷新失败: {e}")
    reg = _api.os_instance().skill_registry
    fresh = set()
    for tool in tools:
        spec = tool_to_spec(server_id, tool); reg.register(spec); fresh.add(spec.id)
    for s in [s for s in reg.all_including_disabled()
              if s.kind == "mcp" and s.mcp_server == server_id and s.id not in fresh]:
        reg.unregister(s.id)
    _api.os_instance().skill_store.save(reg)
    return {"ok": True, "tools": len(fresh)}


class MdSkillPayload(BaseModel):
    content: str = ""
    package_b64: str = ""            # OpenClaw 包(zip)的 base64;有则优先,解包取 SKILL.md
    id: str = ""
    acknowledged: bool = False


class MdSkillPatchPayload(BaseModel):
    enabled: bool | None = None
    triggers: list | None = None            # 改触发词(免解压重打包);None=不动


@app.post("/api/ahs/md-skills")
async def md_install(p: MdSkillPayload):
    """上传/注册一个 SKILL.md 指令技能(单 .md 文本 或 OpenClaw zip 包)。解析→审核→持久化。"""
    from foxclaw.skills.markdown_skill import parse_skill_md, extract_skill_package
    content, want_id, want_version = p.content, (p.id or None), ""
    if p.package_b64:
        import base64 as _b64
        try:
            content, meta = extract_skill_package(_b64.b64decode(p.package_b64))
        except ValueError as e:
            raise HTTPException(400, f"OpenClaw 包解析失败: {e}")
        except Exception as e:
            raise HTTPException(400, f"包解码失败: {e}")
        want_id = want_id or (meta.get("slug") or None)
        want_version = str(meta.get("version") or "")
    try:
        skill = parse_skill_md(content, id=want_id)
    except ValueError as e:
        raise HTTPException(400, f"SKILL.md 解析失败: {e}")
    if want_version and not skill.version:
        skill.version = want_version
    if not p.acknowledged:
        raise HTTPException(409, detail={"needs_ack": True, "warnings": [
            "此技能会把内容注入你 AI 的提示词、影响其行为,仅从可信来源安装"]})
    reg = _api.os_instance().md_skills
    existed = reg.get(skill.id) is not None
    reg.register(skill)
    _api.os_instance().md_store.save(reg)
    return {"ok": True, "skill": skill.to_meta(), "created": not existed}


@app.get("/api/ahs/md-skills")
async def md_list():
    pol = _api.os_instance().skill_policy
    out = _api.os_instance().md_skills.catalog()
    for s in out:
        s["blocked"] = pol.is_blocked(s.get("id", ""))
    return {"skills": out}


@app.get("/api/ahs/md-skills/{skill_id}")
async def md_get(skill_id: str):
    s = _api.os_instance().md_skills.get(skill_id)
    if s is None:
        raise HTTPException(404, "指令技能不存在")
    return s.to_dict()


@app.patch("/api/ahs/md-skills/{skill_id}")
async def md_patch(skill_id: str, p: MdSkillPatchPayload):
    reg = _api.os_instance().md_skills
    s = reg.get(skill_id)
    if s is None:
        raise HTTPException(404, "指令技能不存在")
    if p.enabled is not None:
        s.enabled = bool(p.enabled)
    if p.triggers is not None:
        s.triggers = [str(t).strip() for t in p.triggers if str(t).strip()]
    _api.os_instance().md_store.save(reg)
    return {"ok": True, "skill": s.to_meta()}


@app.delete("/api/ahs/md-skills/{skill_id}")
async def md_delete(skill_id: str):
    reg = _api.os_instance().md_skills
    if reg.get(skill_id) is None:
        raise HTTPException(404, "指令技能不存在")
    reg.unregister(skill_id)
    _api.os_instance().md_store.save(reg)
    return {"ok": True}


@app.post("/api/ahs/run")
async def ahs_run(payload: AHSRunPayload):
    """跑一遍 AHS 执行链:router→planner→composition,全程过 guard+breaker+hooks。
    【P3-2】用 run_async 把同步 compose 放到线程池,避免阻塞事件循环。
    【P2-2】暴露 pending_confirmation/is_pending,供前端识别"待确认"并断点续跑
    (确认后带 confirmed=True + resume_state 重发本接口)。
    【P1】关键操作还需带 confirm_nonce(响应里的 pending_confirmation.confirm_nonce);
    缺/错/过期 → 仍返回 pending_confirmation 并换发新码,不会执行。"""
    from foxclaw.skills import set_loop_session, reset_loop_session
    # 循环检测按执行链分槽:断点续跑复用同一 task_id,否则每次请求独立(消除跨请求污染)
    sid = (payload.resume_state or {}).get("task_id") or f"ahs_{uuid.uuid4().hex[:8]}"
    token = set_loop_session(sid)
    try:
        res = await _api.os_instance().ahs_pipeline.run_async(
            payload.task, confirmed=payload.confirmed,
            resume_state=payload.resume_state,
            confirm_nonce=payload.confirm_nonce)
        return {
            "task": res.task, "route": res.route, "plan": res.plan,
            "composition": res.composition, "success": res.success,
            "summary": res.summary,
            "pending_confirmation": res.pending_confirmation,
            "is_pending": res.is_pending,
        }
    except Exception as e:
        return {"success": False, "summary": f"执行链异常: {e}"}
    finally:
        reset_loop_session(token)


@app.get("/api/ahs/breaker")
async def ahs_breaker_status():
    """断路器状态:各 skill 的失败计数与开闸情况。"""
    br = _api.os_instance().skill_adapter.breaker
    return {"breakers": {sid: br.status(sid) for sid in br._entries}}


@app.get("/api/ahs/hooks/audit")
async def ahs_hooks_audit():
    """钩子审计日志(最近 20 条)。"""
    return {"audit": _api.os_instance().skill_adapter.hooks.recent_audit(20)}
