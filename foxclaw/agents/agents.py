"""三个具体 Agent 实现(拓扑收缩后)。

Agent 拓扑 5 → 3(2026-11-07):
  - impl/supervisor.py  SupervisorAgent(主管:全局统筹/分诊/兜底自答)
  - impl/finance.py     FinanceAgent(财务:收支/预算/风控)
  - impl/office.py      OfficeAgent(办公学习:学习/知识/文档 + 日程/行程)
  - impl/_helpers.py    共享辅助函数 + 多步链式常量

原 `assistant`(通用助手)与 `admin`(行政:日程行程)不再作为独立 Agent 常驻:
  · 角色行为下放为指令技能 `skills/general-assistant`、`skills/schedule-itinerary`
    (frontmatter 标 builtin: true,随包注入命中的 Agent);
  · admin 的行程内核与领域方法(plan_itinerary / handle_cross_request / concede)并入
    OfficeAgent —— 领域逻辑仍归"离内核最近"的 Agent,不放进编排层。
  · 助手原先独占的 skill 工具箱上移到 `mixins/skillbox.py`,三个 Agent 都能用技能。

本文件只做 re-export,保持 `from foxclaw.agents.agents import X` 的历史兼容
(tests/*、foxclaw/__init__.py、pipeline/supervisor_stages.py 仍按此路径取符号)。
"""
from .impl.supervisor import SupervisorAgent
from .impl.finance import FinanceAgent
from .impl.office import OfficeAgent
from .impl._helpers import (
    _with_prior,
    _fmt_mem,
    _dispatch_failed,
    _extract_step_results,
    PRIOR_MAX_STEPS,
    PRIOR_MAX_CHARS,
)

__all__ = [
    "SupervisorAgent", "FinanceAgent", "OfficeAgent",
    "_with_prior", "_fmt_mem", "_dispatch_failed", "_extract_step_results",
    "PRIOR_MAX_STEPS", "PRIOR_MAX_CHARS",
]
