将已经改造好的 vecRecall 接入 Hestia，核心是替换或增强 memory\_adapter 的底层实现。由于 vecRecall 是 local-first 的设计，数据存在本机磁盘，接入路径很清晰，不需要引入外部服务依赖。以下是具体的接入方案。

---

接入前的前置认知：vecRecall 的两种接入形态

vecRecall 目前提供两种使用方式，Hestia 需要根据自身架构选择或组合使用：

形态一：Python 运行时（直接集成） vecRecall 本质上是一个 Python 库，可以通过 pip install "vecrecall @ git+https://github.com/snowfoxHQ/vecRecall.git" 安装。它的记忆工作发生在一个长驻 Python 进程中，该进程持有 embedding 模型，因此重复调用成本极低（约 20–120ms，冷启动后）。Hestia 的 memory\_adapter 可以直接 import 这个库，在进程内完成记忆的写入和检索。

形态二：MCP Server（通过协议接入） vecRecall 也以 MCP Server 的形式存在，对外暴露 vecrecall\_remember、vecrecall\_recall、vecrecall\_context、vecrecall\_list、vecrecall\_forget、vecrecall\_stats 六个工具。Hestia 如果已经将 Skill 接入层迁移到 MCP，可以直接将这个 MCP Server 注册为记忆类工具源，由 memory\_adapter 通过 MCP 协议调用。

建议：优先采用形态一（Python 运行时直接集成）。原因是记忆读写是 Hestia 执行会话的高频路径，MCP 协议虽然标准，但多一层序列化和网络开销；而且 vecRecall 的 Python 运行时本身已经针对“长驻进程、快速重复调用”做了优化，直接嵌入 Hestia 的进程内是最匹配的用法。MCP 形态可以作为可选兼容层保留，用于未来将记忆能力暴露给外部 A2A 编排器。

---

接入步骤：五步完成 memory\_adapter 替换

第一步：安装 vecRecall 运行时

在 Hestia 的 Python 环境中安装：

```bash
pip install "vecrecall @ git+https://github.com/snowfoxHQ/vecRecall.git"
```

目前没有 PyPI 官方包，必须从 GitHub 安装。建议在 pyproject.toml 里锁定 commit 或版本，避免上游变更导致接口不兼容。

第二步：配置 vecRecall 的存储路径与嵌入后端

vecRecall 的所有配置都有默认值，只需覆盖必要项。在 Hestia 的配置文件中（如 .env 或 config.yaml）加入：

```yaml
vecrecall:
  dataDir: '/var/lib/hestia/vecrecall'   # 记忆存储目录，建议挂载持久卷
  wing: 'hestia-finance'                  # 命名空间，按租户/领域隔离
  embedder: 'sentence'                    # 建议用 sentence-transformers，精度高于 bow
  chain: true                             # 启用哈希链，用于审计记忆的防篡改验证
```

关键配置说明：

· dataDir：记忆以明文文件形式存储于此，需纳入 Hestia 的备份策略； · embedder：'sentence' 使用 sentence-transformers，'bow' 是离线兜底，'auto' 会自动选择； · chain：启用后存储量约翻倍，但这是审计记忆不可篡改的基础，财务场景必须开启； · onSensitive：默认 redact 会脱敏凭证类内容，财务场景建议保持默认或设为 reject，防止密钥进入记忆。

第三步：在 memory\_adapter 中实现“记忆类型路由”

这是接入的核心适配层，不是简单的 API 转发，而是在写入 vecRecall 之前完成类型判定和策略标记。memory\_adapter 需要根据 Hestia 的上下文，决定一条记忆以什么“身份”进入 vecRecall：

Hestia 记忆来源 写入 vecRecall 时的策略 契约的 audit.decisions、审批决策、回读快照 标记为 immutable：不走衰减、不参与去重、wing 按 contract\_id 隔离（或使用 topic 标签） 守门人的裁决偏好、审批阈值调整历史 标记为 procedural：单独存储，检索时按场景标签精确匹配，不走语义近似 供应商档案、科目规则摘要 标记为 semantic：走 vecRecall 的图谱/语义检索路径 日常对话中的临时偏好 标记为 episodic：默认路径，允许衰减和去重

关键点：vecRecall 的语义去重（相似度 ≥0.92 合并）和艾宾浩斯衰减对日常记忆是优点，但对审计记忆是风险。memory\_adapter 必须在写入前拦截需要 immutable 的内容，通过 vecRecall 的配置或包装层禁止它们进入衰减/去重管线。

第四步：实现双层隔离（私有 / 共享）的映射

Hestia 原有的“私有层 + 共享层”需要映射到 vecRecall 的 wing 机制上：

· 私有记忆：wing 设置为 agent:（或 tenant:<tenant\_id\>:private），只有对应执行会话可读写； · 共享记忆：wing 设置为 shared（或 tenant:<tenant\_id\>:shared），所有同租户的执行会话可读，但写入权限受限（只有 Router 或特定角色可写）。

vecRecall 的 vecrecall\_list 支持按 wing/topic 结构化列出记忆，Hestia 的 memory\_adapter 可以利用这个能力实现“按契约 ID 检索该契约的所有审计记忆”。

第五步：建立“契约审计记忆”的专用检索通道

财务场景的核心需求是“按契约回放”。memory\_adapter 需要封装一个专用方法 query\_by\_contract\(contract\_id\)，其内部逻辑是：

1. 在 vecRecall 中按 wing（或 topic）过滤出与该契约相关的所有记忆；
2. 对每条记忆验证哈希链（如果启用了 chain），确认未被篡改；
3. 将结果按时间排序返回，供 cc-auditor 做覆盖检查（“该留痕的是否都留了痕”）。

这个专用通道不经过 vecRecall 的语义检索，而是走结构化过滤 + 哈希验证，因为审计回放需要的是确定性，不是相似度。

---

需要显式改造 vecRecall 或包装层的三个点

vecRecall 原生的能力已经覆盖了存储、检索、四层上下文、哈希链，但 Hestia 有三个需求需要额外处理：

① 审计记忆的“不可衰减”保障 vecRecall 的衰减和去重是默认行为，memory\_adapter 需要在写入时通过元数据或包装逻辑强制豁免审计类记忆。如果 vecRecall 的配置不支持 per-record 的衰减豁免，需要在 memory\_adapter 层实现一个“旁路”：审计记忆不进入 vecRecall 的主存储，而是写入一个独立的、只追加的存储路径（可以是 vecRecall 的另一个 wing，配合 chain=true，但关闭该 wing 的衰减调度）。

② Procedural memory 的独立存储 vecRecall 的四层栈（L0-L3）是按“加载策略”分层的，不是按“语义类型”分层的。守门人的策略表、审批阈值、场景标签匹配，这些是规则型记忆，不适合走 embedding 相似度检索。建议在 memory\_adapter 层维护一张独立的策略表（可以是 SQLite 或 Hestia 现有的配置存储），检索时按“场景标签 + 版本”精确匹配，不强行塞进 vecRecall 的语义检索路径。

③ HKC 知识源的注册接口 Hestia 的 HKC（知识库）需要作为 KnowledgeSource 注册到 vecRecall 的检索管线中，让执行会话在一次检索中同时拿到“知识（制度规则、供应商档案）”和“记忆（历史审批、偏好）”。memory\_adapter 需要预留这个融合检索的接口，但具体实现可以放在 HKC 改造阶段完成。

---

验证闭环：接入是否真正生效

接入完成后，用以下四个测试验证：

1. 写入与检索：写入一条测试记忆 → vecrecall\_recall 能按语义命中 → vecrecall\_stats 显示存储增长；
2. 审计记忆豁免：写入一条标记为 immutable 的审计记忆 → 确认它不被衰减、不被去重、query\_by\_contract 能按契约 ID 精确召回；
3. 双层隔离：用 wing=agent:A 写入的记忆，在 wing=agent:B 的检索中不可见；wing=shared 的记忆在两者中都可见；
4. 哈希链验证：启用 chain=true 后，篡改一条记忆文件，验证 query\_by\_contract 的哈希校验能检测到篡改。

如果这四个测试通过，说明 vecRecall 不仅“装上了”，而且按 Hestia 的治理需求正确运行。之后再进行 HKC 改造和 Agent 内核化时，记忆底座就是稳定可依赖的。

---

一句话总结

Hestia 接入 vecRecall 的核心工作是改造 memory\_adapter：安装 Python 运行时、配置存储与嵌入后端、实现“记忆类型路由”（审计记忆豁免衰减/去重）、映射双层隔离到 wing、建立按契约 ID 的结构化审计检索通道。 vecRecall 提供存储、检索、哈希链和四层上下文能力，Hestia 在它之上保留“记忆语义层”——审计记忆的不可变性、procedural memory 的独立存储、以及 HKC 知识源的融合检索接口，这三件事是 memory\_adapter 需要额外承担的，也是财务场景“睡得着”的工程保障。