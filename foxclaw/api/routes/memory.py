"""记忆统计 / 会话历史 / 用户偏好 路由。

从 foxclaw/api.py 原样搬移(纯搬移,零行为变更)。共享 OS 实例经 `_api.current_os()`
晚绑定读取(状态在 `_state`,测试可重绑 `_state._os_instance`);路由用 `@app` 装饰器在 import 时注册。
"""

from fastapi import HTTPException
from pydantic import BaseModel

import foxclaw.api as _api
from .. import app


@app.get("/api/memory/stats")
async def get_memory_stats():
    """记忆中心统计:供前端 vecRecall 面板展示真实数字。"""
    try:
        return _api.os_instance().hmr.stats()
    except Exception:
        return {"backend": "?", "total_memories": 0, "relations": 0}


# ── 会话历史 ───────────────────────────────────────────────
class SessionCreate(BaseModel):
    title: str = ""
    agent_id: str = "supervisor"


class SessionRename(BaseModel):
    title: str


@app.get("/api/sessions")
async def list_sessions(limit: int = 50):
    """会话列表(按更新时间倒序,轻字段不含 messages)。"""
    try:
        return {"sessions": _api.os_instance().sessions.list(limit)}
    except Exception:
        return {"sessions": []}


@app.post("/api/sessions")
async def create_session(req: SessionCreate):
    """新建会话。"""
    return _api.os_instance().sessions.create(req.title, req.agent_id)


@app.get("/api/sessions/{sid}")
async def get_session(sid: str):
    """取单个会话(含完整 messages),供前端恢复对话。"""
    d = _api.os_instance().sessions.get(sid)
    if d is None:
        raise HTTPException(404, "会话不存在")
    return d


@app.post("/api/sessions/{sid}/rename")
async def rename_session(sid: str, req: SessionRename):
    return {"ok": _api.os_instance().sessions.rename(sid, req.title)}


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str):
    return {"ok": _api.os_instance().sessions.delete(sid)}


@app.get("/api/memory/recent")
async def get_recent_memories(limit: int = 20):
    """最近的记忆条目(倒序),供前端右栏"记忆信息条"。轻字段,不含 embedding。"""
    try:
        fn = getattr(_api.os_instance().hmr, "recent", None)
        return {"memories": fn(limit) if callable(fn) else []}
    except Exception:
        return {"memories": []}


@app.get("/api/preferences")
async def list_preferences():
    """列出已记住的用户偏好(供查看/清理误存)。偏好是 shared,任一 Agent 视角一致,
    这里用主管取全量(偏好是 shared,任一 Agent 视角一致)。返回 [{id, content, ...}]。"""
    ag = _api.os_instance().supervisor
    return {"preferences": ag.list_preferences()}


@app.delete("/api/preferences/{pref_id}")
async def delete_preference(pref_id: str):
    """删一条误存的用户偏好(F3:防误报偏好持久注入污染)。返回 {ok}。"""
    ag = _api.os_instance().supervisor
    return {"ok": ag.delete_preference(pref_id)}


# ── 用户档案:称呼 ────────────────────────────────────────────
# 前端首次打开问一句「怎么称呼你」,设置页可改。存的仍是一条 preference
# (每轮注入、跨会话),所以不需要账号/用户表。
class ProfilePayload(BaseModel):
    nickname: str = ""


@app.get("/api/profile")
async def get_profile():
    """当前称呼。没设置过返回空串(前端据此决定要不要显示首次引导)。"""
    return {"nickname": _api.os_instance().supervisor.get_nickname()}


@app.put("/api/profile")
async def update_profile(payload: ProfilePayload):
    """设置称呼。传空串 = 清除。返回生效后的值(已 trim/截断)。"""
    nick = _api.os_instance().supervisor.set_nickname(payload.nickname)
    return {"ok": True, "nickname": nick}
