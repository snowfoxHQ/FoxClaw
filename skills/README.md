# Foxclaw Skill 实现 — AHS 标准的参考落地

> 本目录是 **Agent Harness Skills (AHS) v1.0 标准在 Foxclaw 中的具体实现**。
>
> - **公用标准**(`/agent-harness-skills/`):厂商无关的抽象规范,定义"应该怎样"。
> - **本实现**(本目录):把抽象规范**绑定到 Foxclaw 的真实模块**,定义"在 Foxclaw 里实际怎么做"。
>
> 两者是"规范 vs 参考实现"的关系。读标准看 `/agent-harness-skills/`,读落地看这里。

---

## 标准 → Foxclaw 实现 的映射

AHS 标准里的每个抽象概念,在 Foxclaw 里都有一个**真实模块**承接。这张表是本实现的核心:

| AHS 抽象概念 | Foxclaw 真实模块 | 文件 |
|-------------|----------------|------|
| agent / executor | 五个认知 Agent(主管/财务/行政/办公/助手) | `foxclaw/agents/agents.py` |
| skill-guard(守门·四阶段权限) | `permission.py::evaluate`(四阶段);V2 后经统一门 **PolicyGate** 归一 | `foxclaw/skills/permission.py` `foxclaw/kernel/policy_gate.py` |
| 认知护栏(标准之外) | Harness 三维漂移检测(语义/工具/范围) | `foxclaw/harness/harness.py` |
| skill-memory(记忆) | HMR v2.0 双层记忆适配器 | `foxclaw/core/memory_adapter.py` |
| 审计追踪 | ThoughtChain 思维链 + HookManager.audit_log(有界) | `foxclaw/core/thought_chain.py` `foxclaw/skills/lifecycle.py` |
| execution-contract(契约) | `SkillSpec` / `SkillResult` 数据结构 | `foxclaw/skills/__init__.py` |
| skill-router(路由) | **P1-2 统一 planner** 打分 + 主管分诊 + 管线内 `stage_route`(A) | `foxclaw/planning/planner.py` `foxclaw/skills/pipeline.py` `foxclaw/agents/agents.py` |
| planner(规划) | **P1-2 确定性拆解** `plan()` + 管线 `stage_plan`(A) | `foxclaw/planning/planner.py` `foxclaw/skills/pipeline.py` |
| skill-composition(编排) | skill 级 `compose`(+回滚补偿)+ Agent 级 Orchestration(P3 解耦) | `foxclaw/skills/pipeline.py` `foxclaw/orchestration/` |
| 断路器 / 循环检测 | `CircuitBreaker` + `ToolLoopDetector`(skill 层横切) | `foxclaw/skills/lifecycle.py` `foxclaw/harness/loop_detection.py` |
| 管线内工具调用(A 方案) | Agent 在认知管线内受 PolicyGate 治理地自主用工具 | `foxclaw/pipeline/cognitive_pipeline.py` `foxclaw/agents/base.py` |
| 领域 skill | 财务/学习/行程内核 | `foxclaw/finance/` `foxclaw/learning/` `foxclaw/itinerary/` |
| 模型调用 | 多模型路由(DeepSeek 等) | `foxclaw/core/model_router.py` |

---

## 六个 skill 在 Foxclaw 里的具体落地

### 1. execution-contract → Foxclaw 的 SkillSpec/SkillResult

标准定义了抽象的 SkillInput/SkillOutput。**Foxclaw 用 `foxclaw/skills/__init__.py` 里的
`SkillSpec` 和 `SkillResult` 落地**:
- `SkillResult` 字段:`skill_id/success/output/status/message/loop_warning`——**无 trace_node_id**;审计追踪由 ThoughtChain(Agent `_think` 写节点)+ HookManager.audit_log 独立承担,不塞进结果结构
- `trust_level` → **`SkillSpec.trust_level()` 单一真源**(`permission.evaluate` 直接调用它)按 kind(local/mcp/web_api)推断,SkillAdapter 据此对 mcp 结果降级(原 `permission.TRUST_LEVEL` 双真源 dict 已删,见 V3·P6)
- 输入校验 → `permission.evaluate` **Stage1** 对 `SkillSpec.params` 执行(经 `invoke → PolicyGate.check_action → evaluate`)

> 差异:标准是 schema 描述;Foxclaw 是可运行的 Python dataclass + 真实校验代码。

### 2. skill-router → 主管 Agent 的分诊

标准定义了三步打分算法。**P1-2 后:该算法(否定过滤 0.8×触发命中 + 0.2×描述匹配)统一在 `foxclaw/planning/planner.py::trigger_route`,主管分派 / `skills/pipeline.py::route` / 管线 `stage_route` 都复用**:
- 否定词检测 → 统一原语 `planner.is_negated`;主管另有 `_is_meta` 判元问题
- 主管路由 `SupervisorAgent._route()` 基于 `ROUTING_KEYWORDS`,选定后 `subagents[target].run_task()` 分派
- **A 方案**:管线内 `stage_route` 用同一 `route` 选主 **skill** 写 `ctx.route`(opt-in,默认关)

> 差异:标准是"选一个 skill_id";Foxclaw 有两条落地——① 主管把任务分派给**真实 Agent 实例**;② 管线内直接选 **skill**。共用 `planning.planner` 打分引擎。

### 3. planner → 主管 Agent 的任务分解

标准定义了结构化执行计划。**P1-2 后:任务拆分由确定性统一 planner 承担**:
- `skills/pipeline.py::plan` 用 `planning/planner.py` 的 `split_on`/`PARALLEL_TRIGGERS`/`SPLIT_MARKERS` 把任务拆成 Plan/PlanTask(并行/顺序 + 依赖),不再纯靠 LLM 猜结构
- 管线 `stage_plan` 把任务结构写入 `ctx.plan`(informational)
- 每子任务经 `route` 映射到 skill / 领域 Agent;assumptions/risks 写入 ThoughtChain 节点

> 差异:标准是 JSON 计划模板;Foxclaw 是**确定性 P1-2 拆结构** + 需要生成性内容时另调真实 LLM + 写入真实思维链。

### 4. skill-composition → 主管编排 + Orchestration 引擎

标准定义了串行/并行/混合编排。**Foxclaw 有两层落地**:
- **skill 级**:`skills/pipeline.py::compose` 按计划串行执行各 skill,关键路径失败 → 逆序回滚有副作用步骤(`_rollback` compensate/需人工)
- **Agent 级**:`foxclaw/orchestration/` 跨 Agent 路由(`/api/orchestration/request` → `route_request`,P3 已解耦通用)+ 态势聚合(`/api/orchestration/situation`)
- 断路器 → **`CircuitBreaker`(`skills/lifecycle.py`,skill 层构件,非 Harness)**

> 差异:标准是编排协议;Foxclaw 是 skill 级 compose(带回滚补偿)+ Agent 级真实消息路由/态势引擎。

### 5. skill-guard → Foxclaw 的 Harness(这是最核心的落地)⭐

标准定义了四阶段权限管线。**四阶段全部实现在 `foxclaw/skills/permission.py::evaluate`**(不是 harness.py——harness 是下面另一回事的三维漂移检测)。V2 后经统一门 `PolicyGate.check_action` 调用:

| 标准的四阶段 | Foxclaw 的实现(全在 `permission.py::evaluate`) |
|-------------|--------------|
| Stage1 validate | 校验 `SkillSpec.params` 必填项 |
| Stage2 deny>ask>allow | `PermissionRules` 规则匹配(deny 铁律不可覆盖) |
| Stage3 capability | `side_effect`/`destructive`/`external`/readonly,Fail-closed |
| Stage4 confirm | 有副作用/external/ask 规则且未确认 → 需确认 |

> **V2 统一门(①Step4)**:动作权限(上表)与推理漂移(下方 Harness)归一到 `foxclaw/kernel/policy_gate.py` 的一套 `Verdict` + 统一 `policy.decision.*` 审计事件,底层仍是同一次 evaluate/check,行为逐字不变。
> **V2 管线内 dispatch(A 方案)**:Agent 在认知管线内自主用工具时,`stage_act` 同样经此四阶段——只读自动、有副作用挂起 `needs_confirmation`(dispatch 前拦截 ⇒ 零副作用)。

**Foxclaw 独有的增强(标准之外的护城河)——注意:这是 Harness,与上面的四阶段权限是两回事:**
- **三维漂移检测**(语义/工具/范围)→ 这是标准没有的"认知护栏",防 Agent 跑偏
- **Shadow 校准模式** → 新 Agent 前 N 轮只观测不拦截,自动校准阈值
- **调度层豁免**(`block_on_semantic` + `IS_ROUTER`)→ 主管豁免语义拦截
- **Policy 自学习** → 重复漂移模式自动沉淀为规则

> 差异:标准 guard 只管"工具权限";Foxclaw Harness = 工具权限 + **认知漂移检测**(Foxclaw 原创)。
> 这正是 Foxclaw 作为"认知型 Agent OS"区别于工具型 Agent 的核心价值。

### 6. skill-memory → HMR 双层记忆

标准定义了后端无关的记忆协议。**Foxclaw 用 HMR(`memory_adapter.py`)落地**:
- 召回 → `_recall_memory(task)` 真实调 HMR 向量检索
- 注入 → 召回结果写入 Agent 的 messages 上下文
- 提取持久化 → 办公 Agent 的 `_store_memory` 真实写入 HMR
- private/shared 双层 → Foxclaw HMR 的真实双层架构(by_agent 分布)
- embedding → 真实的 TF-IDF 降级 / sentence-transformers / OpenAI embedding

> 差异:标准是"后端无关协议";Foxclaw 是绑定 HMR v2.0 的真实记忆引擎,带衰减和双层。

---

## 一句话总结

> **公用版 AHS 定义"Agent Harness 的 skill 层应该长什么样"(抽象规范);
> Foxclaw 版把它落到"用 HMR 做记忆、用 Harness 三维漂移做守门、用三 Agent(主管/财务/办公学习)做路由编排、
> 用 ThoughtChain 做审计"的真实实现上。**
>
> Foxclaw 实现不仅满足标准,还在 guard 维度**超出标准**(增加了认知漂移检测这一护城河)。

---

## 对照阅读

- 想了解**标准本身** → 读 `/agent-harness-skills/README_CN.md`
- 想了解**Foxclaw 怎么实现标准** → 读本文件
- 想看**实现代码** → 见上方映射表里的真实模块文件
