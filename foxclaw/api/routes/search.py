"""认知搜索(HSR)端点:内部记忆 + 内部知识 + 联网,统一证据融合。

从 foxclaw/api.py 原样搬移(纯搬移,零行为变更)。共享 OS 实例经 `_api.current_os()`
晚绑定读取;路由用 `@app` 装饰器在 import 时注册。
"""

import asyncio

from fastapi import HTTPException
from pydantic import BaseModel

import foxclaw.api as _api
from .. import app


class SearchQueryPayload(BaseModel):
    query: str
    agent_id: str = "supervisor"
    context: dict = {}
    internal_only: bool = False


@app.post("/api/search/query")
async def search_query(p: SearchQueryPayload):
    """认知搜索(面向 AI/Agent 的证据管线):返回证据图 + 置信度 + 迭代轨迹。
    internal_only=True 时只查内部三源(记忆/知识/Agent),不联网。搜索内核惰性构造。"""
    try:
        rt = _api.os_instance().search_runtime
        result = await asyncio.to_thread(
            rt.search, p.query, p.agent_id, p.context, p.internal_only)
        return result.to_dict()
    except Exception as e:
        raise HTTPException(500, f"搜索失败: {e}")
