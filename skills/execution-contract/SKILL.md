---
name: execution-contract
version: "1.0-foxclaw"
implements: agent-harness-skills/execution-contract
description: Foxclaw 实现版契约。把 AHS 标准的 IO 结构落地到 SkillSpec/SkillResult 数据结构。
bound_modules:
  - foxclaw/skills/__init__.py (SkillSpec / SkillResult / SkillAdapter)
  - foxclaw/skills/permission.py (输入校验 Stage1)
side_effect: read
trust_level: high
---

# Execution Contract(Foxclaw 实现版)

> AHS 标准 `execution-contract` 在 Foxclaw 的落地。标准定义 IO schema,本文件定义 Foxclaw 的真实数据结构。

## 标准 → Foxclaw 真实落地

| AHS 标准 | Foxclaw 实现 | 代码位置 |
|---------|------------|---------|
| SkillInput | `SkillAdapter.invoke` 的入参 | `skills/__init__.py` |
| SkillOutput | `SkillResult` dataclass(字段:`skill_id/success/output/status/message/loop_warning`) | `skills/__init__.py: SkillResult` |
| 审计追踪 | **不在 SkillResult 里**——由 ThoughtChain(Agent `_think` 写节点)+ SkillAdapter 的 `HookManager.audit_log`(有界)双重承担 | `core/thought_chain.py`、`skills/lifecycle.py` |
| trust_level | **`SkillSpec.trust_level()` 单一真源**(`permission.evaluate` 直接调用)按 kind(local/web_api/mcp)推断,`SkillAdapter` 据此对 mcp 结果降级(原 `permission.TRUST_LEVEL` 已删,见 V3·P6) | `skills/__init__.py`、`skills/permission.py` |
| 输入校验 | `permission.evaluate` **Stage1** 校验 `SkillSpec.params`(经 `invoke → PolicyGate.check_action → evaluate`) | `skills/permission.py: evaluate` |

## 关键差异

> 标准是 schema 描述;Foxclaw 是可运行的 Python dataclass + 真实校验代码。
> 注:`SkillResult` **无 `trace_node_id` 字段**——审计追踪独立由 ThoughtChain + HookManager 承担,不塞进结果结构。
