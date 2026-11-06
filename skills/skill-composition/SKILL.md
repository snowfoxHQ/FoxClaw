---
name: skill-composition
version: "1.0-snowfoxclaw"
implements: agent-harness-skills/skill-composition
description: Snowfoxclaw 实现版编排。把 AHS 标准的多 skill 协作落地到主管编排 + Orchestration 引擎。
bound_modules:
  - snowfoxclaw/skills/pipeline.py (★ compose:多 skill 串行执行 + 逆序回滚补偿)
  - snowfoxclaw/skills/lifecycle.py (CircuitBreaker 断路器)
  - snowfoxclaw/orchestration/ (跨 Agent 编排:route_request / gather_situation)
  - snowfoxclaw/agents/agents.py (SupervisorAgent)
side_effect: read
trust_level: high
---

# Skill Composition(Snowfoxclaw 实现版)

> AHS 标准 `skill-composition` 在 Snowfoxclaw 的落地。标准定义编排协议,本文件定义 Snowfoxclaw 用真实编排引擎。

## 标准 → Snowfoxclaw 真实落地

| AHS 标准 | Snowfoxclaw 实现 | 代码位置 |
|---------|------------|---------|
| 多 skill 编排 | `compose(plan)` 按计划串行执行各 skill | `skills/pipeline.py: compose` |
| 失败回滚 | 关键路径失败 → 逆序回滚有副作用步骤(compensate/需人工) | `skills/pipeline.py: _rollback` |
| 跨 Agent 协作 | `/api/orchestration/request` → `Orchestrator.route_request`(P3 已解耦、通用) | `api.py:571`、`orchestration/` |
| 全局态势 | `/api/orchestration/situation` → `gather_situation` | `api.py:554`、`orchestration/` |
| 断路器 | `CircuitBreaker`(三态机,每 skill 独立) | `skills/lifecycle.py: CircuitBreaker` |

## 关键差异

> 标准是编排协议;Snowfoxclaw 有两层:**skill 级** `compose`(多 skill + 回滚补偿,`skills/pipeline.py`)+ **Agent 级** orchestration(跨 Agent 路由/态势,P3 已去领域硬编码)。断路器是 **skill 层**构件(`lifecycle.py`),不属 Harness。
