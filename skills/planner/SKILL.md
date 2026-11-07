---
name: planner
version: "1.0-foxclaw"
implements: agent-harness-skills/planner
description: Foxclaw 实现版规划。把 AHS 标准的任务分解落地到主管 Agent 的 plan 逻辑。
bound_modules:
  - foxclaw/planning/planner.py (★ P1-2 统一拆分原语:split_on / PARALLEL_TRIGGERS / SPLIT_MARKERS)
  - foxclaw/skills/pipeline.py (plan:确定性拆解为 Plan/PlanTask)
  - foxclaw/pipeline/cognitive_pipeline.py (stage_plan:管线内结构规划)
  - foxclaw/agents/agents.py (SupervisorAgent._plan_task / run_task)
side_effect: read
trust_level: high
---

# Planner(Foxclaw 实现版)

> AHS 标准 `planner` 在 Foxclaw 的落地。标准定义计划结构,本文件定义 Foxclaw 用主管真实 LLM 生成。

## 标准 → Foxclaw 真实落地

> ⚠️ **P1-2 后:任务拆分(并行/顺序连接词、`split_on`)由确定性统一 planner 承担**——`skills/pipeline.py::plan` 与管线 `stage_plan` 都用 `foxclaw/planning/planner.py` 的 `PARALLEL_TRIGGERS`/`SPLIT_MARKERS`,不再纯靠 LLM 猜结构。

| AHS 标准 | Foxclaw 实现 | 代码位置 |
|---------|------------|---------|
| 出执行计划(拆结构) | `plan(task)` 确定性拆成 Plan/PlanTask(并行/顺序 + 依赖) | `skills/pipeline.py: plan` |
| 拆分原语 | `split_on` + 并行/顺序连接词表 | `planning/planner.py` |
| 子任务→执行者 | 每子任务经 `route` 映射到 skill / 领域 Agent | `skills/pipeline.py: route`、`agents.py: _route` |
| 管线内结构化 | `stage_plan` 把任务结构写入 `ctx.plan`(informational) | `pipeline/cognitive_pipeline.py: stage_plan` |
| assumptions/risks | 写入 Agent 的 ThoughtChain 节点 | `core/thought_chain.py` |

## 关键差异

> 标准是 JSON 计划模板;Foxclaw 是**确定性 P1-2 planner 拆结构**(可并行/顺序 + 依赖)+ 主管对需要生成性内容时另调真实 LLM + 写入真实 ThoughtChain。
