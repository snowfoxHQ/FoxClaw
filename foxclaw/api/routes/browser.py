"""浏览器子系统(HIR-Browser)端点。

从 foxclaw/api.py 原样搬移(纯搬移,零行为变更)。共享 OS 实例经 `_api.current_os()`
晚绑定读取;路由用 `@app` 装饰器在 import 时注册。
"""

from pydantic import BaseModel

import foxclaw.api as _api
from .. import app


class BrowserActPayload(BaseModel):
    type: str
    target: str | None = None
    value: object | None = None
    meta: dict = {}


@app.get("/api/browser/status")
async def browser_status():
    """浏览器子系统能力/会话状态(available/session_active/launch_error)。"""
    return _api.os_instance().browser_service.status()


@app.get("/api/browser/perceive")
async def browser_perceive():
    """只感知当前页面语义结构(带编号的可交互元素 + 摘要),不改变页面。"""
    return await _api.os_instance().browser_service.perceive()


@app.post("/api/browser/act")
async def browser_act(p: BrowserActPayload):
    """执行一个 Web 动作,返回校验后最新语义结构。"""
    return await _api.os_instance().browser_service.act(p.model_dump())
