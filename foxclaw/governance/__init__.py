"""
Foxclaw Governance Middleware — 框架无关的 Agent 治理中间件（M3）。

把 Foxclaw 的护城河(可审计推理链 ThoughtChain + 三维漂移线束 Harness + 循环检测)
从整套系统里解耦出来,做成**任意 Agent 框架都能挂载的一层护栏**——
LangGraph / OpenAI Agents SDK / 自研 loop 都能用,无需构造 Foxclaw、
不依赖 agents / skills / channels / HMR 任何一块。

用法(三行接入):
    from foxclaw.governance import GovernanceSession
    with GovernanceSession(goal="分析本月理财", allowed_tools=["read","finance"]) as gov:
        r = gov.observe("准备删库", tool="drop_table")   # 越权 → r.blocked=True
        audit = gov.audit()                              # 可审计推理链

见 docs/internal/GOVERNANCE_MIDDLEWARE.md。
"""
from .middleware import (
    GovernanceSession,
    GovResult,
    GovernanceBlocked,
    governed,
)

__all__ = ["GovernanceSession", "GovResult", "GovernanceBlocked", "governed"]
