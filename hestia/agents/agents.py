"""五个具体 Agent 实现（拆分后薄壳）。

步骤 3 把原单一文件按 Agent 拆到 impl/ 子包：
  - impl/supervisor.py  SupervisorAgent（主管）
  - impl/assistant.py   AssistantAgent（助手）
  - impl/finance.py     FinanceAgent（财务）
  - impl/admin.py       AdminAgent（行政）
  - impl/office.py      OfficeAgent（办公）
  - impl/_helpers.py    共享辅助函数 + 多步链式常量

本文件只做 re-export，保持 `from hestia.agents.agents import X` 的历史兼容
（tests/*、hestia/__init__.py、pipeline/supervisor_stages.py 仍按此路径取符号）。
"""
from .impl.supervisor import SupervisorAgent
from .impl.assistant import AssistantAgent
from .impl.finance import FinanceAgent
from .impl.admin import AdminAgent
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
    "SupervisorAgent", "AssistantAgent", "FinanceAgent", "AdminAgent", "OfficeAgent",
    "_with_prior", "_fmt_mem", "_dispatch_failed", "_extract_step_results",
    "PRIOR_MAX_STEPS", "PRIOR_MAX_CHARS",
]
