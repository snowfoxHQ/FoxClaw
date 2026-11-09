"""主管编排端点(全局态势 + 跨 Agent)。

从 foxclaw/api.py 原样搬移(纯搬移,零行为变更)。共享 OS 实例经 `_api.current_os()`
晚绑定读取;路由用 `@app` 装饰器在 import 时注册。
"""

from fastapi import HTTPException
from pydantic import BaseModel

import foxclaw.api as _api
from .. import app


# ── 主管编排端点（全局态势 + 跨 Agent)──────────────────────

@app.get("/api/orchestration/situation")
async def global_situation():
    """主管全局态势:三内核状态汇总 + 跨域告警(驾驶舱视图)。"""
    try:
        return _api.os_instance().supervisor.global_situation()
    except Exception as e:
        raise HTTPException(500, f"态势汇总失败: {e}")


class CrossReqPayload(BaseModel):
    from_agent: str
    to_agent: str
    request_type: str
    payload: dict
    reason: str = ""


@app.post("/api/orchestration/request")
async def cross_request(p: CrossReqPayload):
    """跨 Agent 请求:目标 Agent 用守门人裁决(如行程申请差旅预算)。"""
    try:
        return _api.os_instance().supervisor.cross_request(
            from_agent=p.from_agent, to_agent=p.to_agent,
            request_type=p.request_type, payload=p.payload, reason=p.reason)
    except Exception as e:
        raise HTTPException(500, f"跨 Agent 请求失败: {e}")
