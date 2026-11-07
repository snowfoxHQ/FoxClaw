---
name: skill-router
version: "1.0-foxclaw"
implements: agent-harness-skills/skill-router
description: Foxclaw 实现版路由。把 AHS 标准的任务→skill 匹配落地到主管 Agent 的分诊逻辑。
bound_modules:
  - foxclaw/planning/planner.py (★ P1-2 统一路由原语:is_negated / trigger_route / keyword_hits)
  - foxclaw/skills/pipeline.py (route:在 skill 候选上打分)
  - foxclaw/pipeline/cognitive_pipeline.py (stage_route:A 方案管线内选 skill)
  - foxclaw/agents/agents.py (SupervisorAgent._route / _is_meta:分派到子 Agent)
side_effect: read
trust_level: high
---

# Skill Router(Foxclaw 实现版)

> AHS 标准 `skill-router` 在 Foxclaw 的落地。标准定义匹配算法,本文件定义 Foxclaw 用主管 Agent 实现。

## 标准 → Foxclaw 真实落地

> ⚠️ **P1-2 后:路由/规划的三步打分原语(否定过滤 0.8×触发命中 + 0.2×描述匹配、`is_negated`、`split_on`)统一收敛到 `foxclaw/planning/planner.py` 单一真源**;主管分派、`skills/pipeline.py::route`、管线内 `stage_route` 都复用它,不再各写一套。

| AHS 标准 | Foxclaw 实现 | 代码位置 |
|---------|------------|---------|
| 三步打分 / 选主 | `trigger_route(task, candidates)` 统一引擎 | `planning/planner.py: trigger_route` |
| 否定词检测 | `is_negated`(统一原语);主管另有 `_is_meta` 判元问题 | `planning/planner.py`、`agents.py: _is_meta` |
| 触发词匹配 | 候选的 triggers 命中(skill 侧)/ `ROUTING_KEYWORDS`(Agent 侧) | `planning/planner.py`、`agents.py` |
| 选 skill(A 方案) | 管线内 `stage_route` 用 `skills.pipeline.route` 选主 skill 写 `ctx.route` | `pipeline/cognitive_pipeline.py: stage_route` |
| 兜底 fallback | 无法归类 → 主管自答(助手/行政已下放为 `general-assistant` / `schedule-itinerary` 技能) | `pipeline/supervisor_stages.py: sv_route / sv_dispatch` |
| 实际分派 | 主管 `subagents[target].run_task()` | `agents.py: SupervisorAgent.run_task` |

## 关键差异

> 标准是"选一个 skill_id";Foxclaw 有**两条落地**:① 主管把任务分派给一个**真实 Agent 实例**(agents.py);② A 方案下管线内 `stage_route` 直接选 **skill**(opt-in,默认关)。两者共用 `planning.planner` 打分引擎。
> 元问题(你是谁/能联网吗)由主管自答,跳过分派——Foxclaw 特有。
