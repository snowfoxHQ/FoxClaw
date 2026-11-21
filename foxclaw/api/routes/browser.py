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
    confirmed: bool = False


@app.get("/api/browser/status")
async def browser_status():
    """浏览器子系统能力/会话状态(available/session_active/launch_error)。"""
    return _api.os_instance().browser_service.status()


@app.get("/api/browser/perceive")
async def browser_perceive():
    """只感知当前页面语义结构(带编号的可交互元素 + 摘要),不改变页面。
    SC-03:经统一感知准入边界(observe)——权限放行后才感知;结果带 wrapped/source/taint;
    未授权 fail-closed 拒绝,不再直接返回原始感知。"""
    r = await _api.os_instance().observe(source="browser", scope="page:*", sensitivity="private")
    if not r.allowed:
        return {"ok": False, "denied": True, "reason": r.reason,
                "decision_id": r.decision_id}
    raw = r.raw if isinstance(r.raw, dict) else {"ok": True, "semantic_state": r.raw}
    resp = dict(raw)
    resp.update({"wrapped": r.wrapped, "source": r.source, "taint": r.taint,
                 "decision_id": r.decision_id})
    return resp


@app.post("/api/browser/act")
async def browser_act(p: BrowserActPayload):
    """执行一个 Web 动作,返回校验后最新语义结构。
    SC-08:先经操作意图授权边界(os.browser_act → check_action),deny/confirm fail-closed
    拒绝,不再直接执行;write 副作用动作需前端带 confirmed=true 才放行。"""
    payload = p.model_dump()
    confirmed = payload.pop("confirmed", False)
    return await _api.os_instance().browser_act(p.type, payload, confirmed=confirmed)
