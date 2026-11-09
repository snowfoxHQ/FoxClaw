"""断点续跑现场(P1):上次被中断的任务 + 各自跑到哪一步。

只读端点,不触发新的日志写入。数据源:
  · `Foxclaw.interrupted_tasks` —— 启动时 `RestartRecovery.scan_interrupted()` 的快照
    (进行中且 boot_id 非本次启动 → 必然是被中断的);
  · `RestartRecovery.steps_of(task_id)` —— 步骤级 checkpoint 回放:`kind=stage` 是管线阶段,
    `kind=tool` 是工具调用(带指纹,续跑时可直接复用结果)。

续跑方式:`POST /api/task`,原 task 文本 + `context={"_resume_task_id": "<task_id>"}`。
"""

import foxclaw.api as _api
from .. import app


@app.get("/api/recovery/status")
async def recovery_status():
    """中断任务清单 + 每任务最近步骤 + 续跑所需 context(直接可回灌 /api/task)。"""
    os_ = _api.current_os()
    if os_ is None:
        return {"enabled": False, "interrupted": [], "plan": {}}
    rec = getattr(os_, "recovery", None)
    out = []
    for t in getattr(os_, "interrupted_tasks", []) or []:
        steps: list = []
        try:
            steps = rec.steps_of(t.task_id) if rec is not None else []
        except Exception:
            steps = []
        out.append({
            "task_id": t.task_id,
            "description": t.description,
            "session_id": t.session_id,
            "channel": t.channel,
            "status": t.status,
            "steps_count": len(steps),
            "last_steps": steps[-10:],      # 只回最近 10 步,避免响应体失控
            "resume_hint": {"endpoint": "/api/task",
                            "context": {"_resume_task_id": t.task_id}},
        })
    plan = getattr(os_, "recovery_plan", None)
    return {"enabled": rec is not None, "interrupted": out,
            "plan": plan.to_dict() if hasattr(plan, "to_dict") else {}}
