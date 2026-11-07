---
name: skill-guard
version: "1.0-foxclaw"
implements: agent-harness-skills/skill-guard
description: Foxclaw 实现版守门 skill。四阶段权限管线落地在 permission.py;Foxclaw 另有 Harness 三维漂移检测(标准之外的认知护城河);V2 后两者经统一门 PolicyGate 归一。
bound_modules:
  - foxclaw/skills/permission.py (★ 四阶段权限管线 evaluate)
  - foxclaw/kernel/policy_gate.py (★ V2 统一门:权限 + 漂移归一)
  - foxclaw/harness/harness.py (三维漂移检测,认知护栏)
  - foxclaw/skills/__init__.py (SkillAdapter.invoke 调 check_action)
  - foxclaw/agents/base.py (_think → _reasoning_result → check_reasoning)
side_effect: read
trust_level: high
---

# Skill Guard(Foxclaw 实现版)

> 本文件是 AHS 标准 `skill-guard` 在 Foxclaw 的具体实现说明。
> 标准定义"应该怎样守门",本文件定义"Foxclaw 用哪些真实模块守门"。

## 标准四阶段 → Foxclaw 真实落地

> ⚠️ 四阶段**全部实现在 `foxclaw/skills/permission.py::evaluate`**(不是 harness.py——harness 做的是下面另一回事的三维漂移检测)。`SkillAdapter.invoke` 经统一门 `PolicyGate.check_action` 调用 evaluate。

| AHS 标准阶段 | Foxclaw 实现 | 代码位置 |
|-------------|------------|---------|
| Stage1 validate | 校验 `SkillSpec.params` 必填项 | `skills/permission.py: evaluate`(Stage1) |
| Stage2 deny>ask>allow | `PermissionRules` 规则匹配(deny 铁律不可覆盖) | `skills/permission.py: evaluate`(Stage2) |
| Stage3 capability | `side_effect` + `destructive` + `external` + readonly 模式,Fail-closed | `skills/permission.py: evaluate`(Stage3) |
| Stage4 confirm | 有副作用/external/ask 规则且未确认 → 需确认 | `skills/permission.py: evaluate`(Stage4) |

**V2 统一门(①Step4)**:动作权限(上表,`check_action`)与推理漂移(下节 Harness,`check_reasoning`)现归一到 `foxclaw/kernel/policy_gate.py` 的一套 `Verdict`;非 allow 发统一 `policy.decision.*` 审计事件。底层仍是同一次 evaluate/check,行为逐字不变。

**V2 管线内 dispatch(A 方案)**:Agent 在认知管线内自主用工具时,`stage_act` 同样经 `SkillAdapter→PolicyGate.check_action` 走本四阶段——**只读自动、有副作用挂起 needs_confirmation(权限门在 dispatch 前拦截⇒零副作用)**,与直呼工具面同一治理。见 `foxclaw/pipeline/cognitive_pipeline.py`。

## Foxclaw 超出标准的部分(护城河)⭐

标准的 guard 只管"工具权限"。Foxclaw 的 Harness 在此之上**增加了认知护栏**,这是 Foxclaw
作为认知型 Agent OS 的核心差异:

### 1. 三维漂移检测(标准没有)
`harness.check(node)` 对每个 ThoughtChain 推理节点计算:
```
delta = 0.5·语义漂移 + 0.3·工具漂移 + 0.2·范围漂移
```
- 语义漂移:推理内容与 Agent 职责基准的距离(防"财务 Agent 去写诗")
- 工具漂移:是否超出 ALLOWED_TOOL_CATEGORIES
- 范围漂移:是否超出任务初始范围

判定:`< warn` PASS / `warn~block` WARN / `≥ block` BLOCK(抛 HarnessBlockedError)。

### 2. Shadow 校准模式(标准没有)
新 Agent 前 `FOXCLAW_SHADOW_ROUNDS` 轮只观测不拦截,收集真实漂移分布自动校准阈值,
避免阈值拍脑袋导致误杀。

### 3. 调度层豁免(对应标准的 mode,但 Foxclaw 特化)
- `IS_ROUTER=True` 的主管 Agent → `block_on_semantic=False`
- 主管收到任意输入豁免语义拦截(接收一切是其本职),但工具/范围越权仍拦
- 领域 Agent 保留完整三维拦截

### 4. Policy 自学习(标准没有)
重复出现的漂移模式自动沉淀为 Policy 规则,Policy 版本递增。

## 与标准的关系

> Foxclaw 的 guard = AHS 标准的工具权限管线 + Foxclaw 原创的认知漂移检测。
> 满足标准的同时,在认知维度超出标准。
