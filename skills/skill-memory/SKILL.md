---
name: skill-memory
version: "1.0-foxclaw"
implements: agent-harness-skills/skill-memory
description: Foxclaw 实现版记忆 skill。把 AHS 标准的后端无关记忆协议落地到 HMR v2.0 双层记忆引擎。
bound_modules:
  - foxclaw/core/memory_adapter.py (HMR 适配器)
  - foxclaw/agents/base.py (_recall_memory / _store_memory)
side_effect: read
trust_level: high
---

# Skill Memory(Foxclaw 实现版)

> 本文件是 AHS 标准 `skill-memory` 在 Foxclaw 的具体实现说明。
> 标准定义"后端无关的记忆协议",本文件定义"Foxclaw 用 HMR 怎么实现"。

## 标准三操作 → Foxclaw 真实落地

| AHS 标准操作 | Foxclaw 实现 | 代码位置 |
|-------------|------------|---------|
| recall 召回 | `_recall_memory(task)` 调 HMR 向量检索 | `agents/base.py` |
| inject 注入 | 召回结果写入 Agent messages 上下文 | `agents/base.py` |
| extract 提取 | `_store_memory()` 写入 HMR | `agents/agents.py: OfficeAgent` |

## Foxclaw 的真实记忆引擎(HMR v2.0)

标准说"后端无关",Foxclaw 绑定的真实后端是 **HMR(Hierarchical Memory Runtime)**:

### 双层架构(对应标准的 private/shared)
- **私有层**:每个 Agent 只能召回自己的记忆(by_agent 分布)
- **共享层**:标记 shared 的记忆所有 Agent 可召回(如主管的全局决策)
- 真实统计:`/api/memory/stats` 返回 private/shared/by_agent/relations

### Embedding(对应标准的相关性计算)
Foxclaw 实际用三档(自动探测):
- OpenAI Embedding(配了 openai key)
- Sentence-Transformers(装了 800MB 包)
- TF-IDF n-gram(降级默认,无需额外依赖)

### 知识图谱(标准未强制,Foxclaw 增强)
HMR 维护记忆间的关系图(relations),办公 Agent 入库时自动抽取知识关系。

### 衰减(对应标准的 decay_management)
HMR 按时间 + 引用频率管理记忆生命周期。

## 与标准的关系

> 标准是抽象协议(任何向量库/KV 都能实现);
> Foxclaw 是绑定 HMR v2.0 的真实引擎,带双层、知识图谱、三档 embedding。
