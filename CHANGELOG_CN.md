# 更新日志

从 0.1.0 首次公开到当前版本的变更记录，最新的排在最前面。

英文版:[CHANGELOG.md](CHANGELOG.md)。

## 0.1.8 — 2026-11-22

**移除:内置联网搜索与地图 Key**

- `web_search` 全链路移除:工具声明与能力标签、执行分支、自动联网增强、百度检索端口、技能条目、提示词措辞,以及 `BAIDU_SEARCH_*` 配置。对应的核验器与污点映射一并删除。
- 个人数据闸门迁到 `foxclaw/core/personal_data.py`,「私人数据不出网」不再依附于某一个具体通道。
- 主管提示词改为如实说明系统不具备联网能力,不再暗示可以查实时信息。
- 高德地图一并移除:`AMAP_MAPS_API_KEY` / `AMAP_JS_KEY` / `AMAP_JS_SCODE`、`/api/config/amap` 路由、行程地理编码客户端、前端 JS 地图加载器。行程距离改为按坐标直线估算,地图面板用 `map_data` 渲染内置 SVG 示意图。

**桌面操作安全底座与操控能力（Computer Use）**

操控电脑的能力在授权与治理底座之上落地，底座先行合龙，确保「系统被骗时不该闯祸」在动作真正执行之前就被拦住。

**能做什么、不能做什么（能力与边界）**

- 浏览器：网页**感知**（`/api/browser/perceive`，读取页面语义状态）与网页**六类动作**（`navigate` / `click` / `type` / `scroll` / `extract` / `wait`）。两条路径现在都经过统一授权：感知走独立观察通道，动作走操作意图授权，写类动作（navigate/click/type）需显式确认后才放行。
- Windows 桌面操控已端到端实现。**感知**——`screen_capture`（截屏）、`window_list`（窗口列举）、`ui_tree`（UI Automation 元素树）、`ocr`（屏幕文字识别）——各自经观察通道授权，除非 `FOXCLAW_OBSERVE_ALLOW` 开闸否则 fail-closed。**操作**——`desktop_act` 覆盖鼠标键盘、窗口、剪贴板、shell、文件与 UI 动作——经操作意图授权，每个写动作需确认后才执行。两层都以 Agent 工具声明暴露给模型（`screen_capture` / `window_list` / `ui_tree` / `ocr` / `desktop_act`）：感知工具按 read 运行，`desktop_act` 按 write 运行并被确认闸拦住，未确认的桌面动作绝不执行。
- **写动作确认是一次性确认码，不是一个布尔值**：写动作首次请求只返回 `confirm_nonce`（绑定动作 + 参数，5 分钟过期，用一次即废）；`/api/desktop/act` 必须同时带 `confirmed=true` 和该确认码才执行，只带 `confirmed=true`、换参数、过期或重放一律拒绝。
- **Agent 发起的写动作停下来交给用户**：工具循环按具体动作裁决——读动作（`file:read` / `clipboard:read`）直接执行；写动作生成待确认项，经 `run_task` 返回体的 `pending_actions` 交给调用方，确认码不进入模型上下文，模型自己放不了行。用户确认后调用 `/api/desktop/act` 执行这一条原样动作。
- **高危动作默认拒绝**：未设置 `FOXCLAW_DESKTOP_ACT_DENY` 时 `shell:run` 与 `file:delete` 默认 deny，确认码也救不回来；显式设置该变量即由使用者接管（`none` 表示不加默认拒绝）。
- **执行层范围限制**（所有入口共用，授权之后的第二道闸）：
  - 文件读/写/删与 shell 的 `cwd` 必须落在工作目录内（`FOXCLAW_DESKTOP_ROOTS`，默认：用户主目录 + 系统临时目录）；路径先展开 `~`、`..`、符号链接再判断。
  - 敏感路径永远拒绝，读也拒：`.ssh` / `.aws` / `.gnupg` / `.kube` 等凭据目录、`.env`、私钥与证书文件、`.git-credentials` 等，以及系统目录；`FOXCLAW_DESKTOP_PROTECT` 可追加。
  - shell 命中危险命令（递归删除、格式化、关机、改注册表、下载即执行、编码执行等）或涉及敏感路径即拒绝；超时上限 120 秒，输出超过 64 KB 截断。命令黑名单挡不住刻意变形的命令，所以 `shell:run` 仍默认 deny。
- 现在还**不能做**的：macOS/Linux 上的桌面操控——感知与操作运行时目前仅 Windows（WinRT / UI Automation），跨平台端口已预留。
- 网页端确认卡片：每条待确认桌面动作一张卡，原样展示动作与参数，确认即调 `/api/desktop/act` 执行这一条，拒绝不发请求；子 Agent 产生的待确认项会上交给主管返回体。还**没接上**的：消息渠道里的确认回复。

**安全操作规范**

- 授权单位从「工具名」改为「归一化操作意图」（domain/action/target/effect）：换一个工具名表达同一操作，命中同一授权与拒绝规则，等价路径绕不过 deny。
- 感知权限与操作权限彻底分离：拿到「看」的授权不等于拿到「动手」的授权；普通 `read` 不放行屏幕采集。
- 感知数据强制不可信：网页感知结果进入决策流前一律 `wrap_untrusted`，模型总结、压缩、改写都不降低来源污点；页面里的「请执行 X」不会被当成用户授权。
- 工具路径补齐统一准入：`recall_memory` 等工具不再绕过 `PolicyGate`，与技能路径共用同一入口、同一 `decision_id` 与审计事件。
- 浏览器动作接入操作意图授权（`/api/browser/act`）：deny 与未确认的写动作一律 fail-closed 拒绝。
- 守门人进程 fail-closed 补齐回归测试：守门人不可达时，`evaluate` 拒绝、`mcp_call` 与 `execute_web` 返回 blocked，不退化成本地直发。
- 执行契约回滚（compensate）生产触发链核验通过：任务失败时按逆序补偿已执行的副作用步骤，补偿失败进人工处理。

**宪法（《桌面操作宪法》DOC-01 ~ DOC-07）**

这一版是 Computer Use 上位规范《桌面操作宪法》的工程落地。落实到代码的条款：

- **意图归一化（DOC-03）**：授权与拒绝以规范化操作意图为依据，而非工具名；拒绝后的等价路径命中同一意图规则。
- **感知/操作分离（DOC-04）**：感知通道（读屏/读页）与操作通道（点击/输入/写文件）分别授权，互不自动继承。
- **感知数据不可信（DOC-05）**：屏幕与网页感知数据强制标记为不可信来源并传播污点，不得因模型总结而变可信。
- **核验口径（DOC-02）**：「已生效 = 有生产调用点；未接线 = 代码写了但没人调」——本版每一条都以「安全要求 → 执行点 → 生产调用链 → 测试证据」四列核验，不采信文档描述。

宪法里此前标记「预留」的桌面执行进程、屏幕采集、UI Automation 运行时现已在 Windows 上落地并通过验证；非 Windows 运行时仍为预留。

## 0.1.7 — 2026-11-21

**记忆写入准入（M1/M2/M5）**

- 每条记忆写入现在带来源标签：只有显式用户信号才产生 `[stated]`，agent 结论、搜索结果与工具产出一律 `[derived]`。可选严格模式（`FOXCLAW_MEMORY_SOURCE_STRICT`）拒绝无用户来源声明的写入。
- 写入校准把可查数据、单次提及、未逐条确认的 agent 细节、任务过程状态挡在长期记忆之外；已入档的事实视为已记住，换措辞复写不再产生第二条。
- 反投毒底线按效果而非措辞判定「少诚实／少安全／少质疑／无条件讨好／压抑不同意见」，委婉变体同样拒绝，任何用户指令不可覆盖。

**隐私与尽力而为（M4/M6）**

- 新增未成年人身份信息、受保护属性两类阻断类目。同意路径上命中阻断类目的整段省略，绝不写泛化替身；「忘记 X」删除对应记忆及其派生内容。健康与受保护属性仍走原有三档确认流程。
- 记忆失败按尽力而为处理：限频记日志后主任务继续，不重试风暴、不刷屏、不主动提议「我帮你记住这个」。
- M3 版本化写入保持设计预留：HMR 适配层持有可重入锁，无跨会话并发写场景；等多渠道并发真正出现时再实现 `if_version` 合并。

## 0.1.6 — 2026-11-19

失败的模型调用不能再被记成一次成功的回答。每次调用带自己的状态而不是一个 Router 级开关，每个任务凭证据结算而不是凭错误字符串，记忆层拒绝从未被核验的产出。随包运行时的检测、健康上报与测试注册一并收紧。

**调用状态**

- 新增 `foxclaw/core/call_status.py`：每次模型调用一个 `CallStatus`，由任务级 `TaskCallCollector` 持有（contextvar 注册）。收集器跟着任务走（含跨 `asyncio.to_thread`），同一 Router 实例上并发跑的两个任务不会互相读到对方的结论——这正是「任务 A 降级、任务 B 报成功」的根因。Router 级 `_call_degraded` 字段与已废弃的 `take_call_degraded()` 一并移除。
- 六个状态，初始态为 `PENDING`：`REAL`（首个 provider 即成功）、`FALLBACK`（主 provider 失败、回退成功）、`STUB`（未配置任何模型）、`DEGRADED`（全部 provider 失败 / 流式中断 / 空响应）、`UNSUPPORTED`（provider 不支持该能力，不算失败）。调用路径返回时未置终态，按实现 bug 处理。
- 流式路径中断时回退到非流式调用，并携带 `partial` 标记已经输出的部分。`UNSUPPORTED` 与 `DEGRADED` 彻底分开：「这个 provider 不会用工具」不再长得像「模型坏了」。
- 工具循环把六阶段履历（`requested` / `executed` / `succeeded` / `rejected` / `failed` / `verified`）记进同一个收集器，包括旧的纯字符串返回路径——工具路径直答视为「无状态对象」，不再崩在 `.state`。

**任务结算与记忆准入**

- `call_status.py` 的 `settle_task_status` 是唯一结算入口：`COMPLETED`（必需业务结果 + 后置条件全满足）、`PARTIAL`（部分真实结果）、`FAILED`（无有效结果）、`INTERRUPTED`（主动中断）、`BLOCKED`（Harness 停止 / 待确认）。结算只读收集到的状态与工具履历，绝不靠错误字符串判成败。
- 降级不等于成功：`STUB` / `DEGRADED` / `PENDING` 只有在同时存在**已核验的工具事实**时才降为 `PARTIAL`，否则 `FAILED`。等待确认的任务结算为 `BLOCKED` 并带独立的 `task_status` 字段，不再被当成已完成任务写进记忆。
- `admit_memory` 是模型产出的唯一准入闸门：事实通道要求 `executed` 与 `verified` 双证据（被拒、超时、未核验的工具输出不能标 fact，「内容来自工具」本身不构成事实资格）；derived 通道一律拒绝 `STUB` / `DEGRADED` / `PENDING` 下的产物。
- 财务账本的真落账会登记为一条已核验工具事实，因此「账本成功、分析降级」结算为 `PARTIAL` 而不是 `FAILED`；dry_run 不登记，有对照测试防止误升。

**健康、检索与工具结果**

- `GET /health/ready` 按记忆运行时自报的 backend 判定：stub 后端不再报成 `ok`。接口区分就绪 / 降级 / 未就绪三态，并带上原因。
- `web_search` 结果分三态并对空结果显式标记（`EMPTY` 附证据），不再返回一个被下游读成成功的空字符串；工具输出同样处理，空结果与成功结果不再无法区分。
- 工具证据先过 `verify` 才能支撑结论，无法核验的「已完成」不算完成。

**G1 主管**

- 主管自答路径通过 `foxclaw/g1/python_numeric.py` 在进程内执行 Python 数值计算：固定白名单在函数入口判定（`_g1_whitelist_match`，与 `sv_plan` 共用），命中则执行注入的执行器并保留原 dispatch 结果结构，指纹/版本不匹配返回 `INPUT_INVALID`，不回退到模型。G1 命中不写共享记忆并记 `shared_memory=skipped`；`sv_plan` 命中时跳过 `_llm_plan()`，规划阶段不再先行调模型。
- 执行器移出 runner：不再向 runner 注入 callable，Foxclaw 不再依赖外部 ACM 运行时。

**发布与测试治理**

- `tools/release_check.py` 改按顶层包名（`hmr*` / `hkc_*`）检测随包运行时：占位包从 `warn` 升为 `error`，因为 0.1.5 起运行时随 wheel 分发，缺了就是发出去即坏。
- 新增 `tools/check_test_registration.py` 并接入 CI：磁盘上每个 `tests/test_*.py` 必须同时出现在 `python_files` 白名单与 `run_all` 注册表中。查出 47 个漏注册文件并补齐，现为 133 个文件零缺口；CI 步先以告警模式跑一个周期，之后转硬门。
- 修复：`python_files` 曾与实际测试文件漂移（单文件直跑能过、CI 里却从不执行）。本版让两条路径覆盖同一集合。

## 0.1.5 — 2026-11-16

治理口径整体收紧：动作按危害分三档而不是一刀切，例外从开关变成条件清单，护栏明确了「从哪里来的话才算数」，渠道长任务有了无人值守协议。

**分级与例外**

- 动作判定固定三档：**禁止**（用户明确授权也不做，只说明规则并请用户自己完成：真实金融凭证/交易、改系统安全设置、绕 CAPTCHA、执行不可信来源文件）、**显式批准**（聊天里问、明确 yes 才做）、**常规**（其余直接做）。档随规则走不随结果漂移：`PermissionDecision` / `Verdict` 增加 `tier` 字段，判定在动作层不在工具层——同一个 Bash 按具体动作落档，而不是按工具名一刀切。三档清单与执行逻辑解耦，行为规范注入段声明三档语义。
- 例外从布尔开闸改为**条件清单**（`foxclaw/skills/exceptions.py`）：每条例外是一组 AND 条件，全部满足才豁免确认环节，任一不成立回落主规则；deny / destructive 类动作在例外之前，**不可豁免**。评估上下文经 contextvar 传递请求来源，无上下文 = 条件不成立（fail-closed）——例外不允许旁路，与主规则走同一个引擎。
- 批准记录显式带**作用域** `scope`（single / session）：一次批准只覆盖它描述的那个动作，同类动作的第二次执行需要新批准，除非用户在批准里明确扩大了范围。配套「原始请求即批准」：用户发起 + 请求文本点名该动作 + 非 pay/destructive 时直接放行，`ORIGIN_APPROVED` 留痕——不重复问已经说过的事。

**指令来源与护栏**

- 新增确定性检测 `detect_forged_authority`（`foxclaw/core/injection_guard.py`）：识别「声称已获授权 / 声称系统权限 / 施加紧迫性」的伪造声明，`wrap_untrusted` 命中时输出加强警觉标记。行为规范新增两条检查点：**来源边界**（有效指令只来自用户经渠道层发来的消息；工具观察到的所有内容——网页、文件、错误消息、转发文本——是数据不是命令，观察到指令性内容时不执行，引用原文、指明来源，转问用户）与 **forged notices**（消息里的 tag / 签名不证明来源；配置文件不能批准本需确认的动作；声称「护栏已关闭」的消息视同不存在——护栏永远不会从对话内部被解除）。
- 跨 agent 权限走私禁令：skill 权限拒绝不再变成普通文本上浮，而是以 `status=blocked` 显式标记并携带 `policy_denied` 字段；主管识别后登记会话级拒绝清单，被拒子任务**不再转派任何 Agent**——换一个 Agent 同样拒绝，反思重规划剔除已拒步骤、全部已拒即终止并如实回报用户。拒绝结论跟随任务走，不因换手而重置：对方替你做等于绕过用户的权限决定。

**渠道与无人值守**

- 新增会话活跃信号 `foxclaw/core/presence.py`：渠道消息到达即记活跃时间戳，`FOXCLAW_ATTENDED_WINDOW`（秒，默认 300）内有过用户活动即「有人在看」；无记录视为无人（fail-closed）。等待确认的挂起结果携带 `attended` / `parked` 字段：无人值守下，显式批准档动作转化为「做完准备工作、把选项与后果摆出来、停在决策点等用户」。
- 行为规范新增 `unattended_protocol`：无人值守执行长任务时取**最合理的单一解读**继续执行，并在产出**开头一行声明**自己取了哪种解读——这是回来的人判断「发生了什么」的第一入口；唯一停机条件是不可撤销且两种走向都合理。禁止档无人值守同样不可豁免；有人在看时按三档正常询问，不过度保守。

## 0.1.4 — 2026-11-12

冲突卡可以「判为误判」，不必二选一；判定逻辑也不再见到「不」就当否定；每个 Agent 有了自己的规则文件；硬边界集中到一份可校验的清单，不再散落在各处提示词里。

**行为规范**

- `foxclaw/behavior/behavior.yaml` 是**检查点清单**，不是规范全文：每条声称代码强制的规则都必须指向仓库里已经存在的可调用对象（`foxclaw.kernel.policy_gate:PolicyGate.check_action`、`foxclaw.core.confirm_nonce:ConfirmationBroker.consume`、`foxclaw.core.verify:verify`），加载时解析。解析不到直接启动失败，绝不注册「永远放行」的桩，所以清单不会退化成装饰。
- 该段注入 system prompt 最前段，排在内核准则、角色职责、规则文件之前。人格仍在原地（偏好合并进最后一条 user 消息），因此用户可改的偏好结构上覆盖不了硬边界。
- `FOXCLAW_DATA_DIR` 下的 `behavior.local.yaml` 可以新增规则、可以把 `enforce` 收紧；但放宽或省略删除 `prohibitions` / `confirmations` 里的规则会直接报错。本地文件语法写错或版本过期只告警并忽略，不会把应用锁死；内置文件的问题仍然致命。
- 代码侧尚未实现的规则（`no_privacy_upload`、`no_impersonation`）标成 `enforce: prompt` 并附 `note` 说明缺什么，不假装已经强制。
- 设 `FOXCLAW_BEHAVIOR=0` 可整体关闭。

**记忆**

- 高敏感内容写入长期记忆前先过一道确定性闸门：凭据 / 证件号 / 健康 / 卡号模式加 Luhn 校验，不调模型。`FOXCLAW_SENSITIVE_WRITE_MODE` 三档：`audit`（默认，只记录）、`warn`、`enforce`。`enforce` 下内容**不落库**，转成待确认项由用户点头；`approve_sensitive_write` 是全仓唯一允许传 `consent=True` 的地方。
- 判定刻意宁可多拦：误判的代价是用户多点一次确认，漏判则等于这条规矩不存在。普通金额和订单号被明确排除，否则财务 Agent 直接不可用。
- 新增 `GET /api/memory/pending`、`POST /api/memory/pending/{id}/approve` 与 `/reject`；记忆中心面板列出排队中的写入，可就地「记住」或「丢弃」。写入被拦时 Agent 会在思考链里说明，不静默丢弃。
- 行为规范里 `confirm_high_sensitivity` 升为 `enforce: both`，检查点指向真实闸门。

**内容安全**

- 新增内容闸门 `foxclaw/core/content_guard.py`：确定性判定，不调模型。用户请求与模型输出两端各过一道，命中即用固定话术切断（「我们换个话题聊聊吧。」/ "Let's talk about something else."），不解释、不延伸、不反问、不给替代方案 —— 拒绝本身不能变成信息泄漏或继续纠缠的入口。
- 内置规则只覆盖**请求型教唆**（制毒制爆、诈骗洗钱、入侵他人系统、伪造证件、自杀方法、未成年性内容），措辞窄、误伤低，开箱即用。「提及」不算「索要」：讲炸药发展史的书、洗钱量刑的论文、订单号、`91年出生` 都不拦。
- 平台审核要求的政治 / 色情 / 暴力大类词表在数万条量级，硬编码进代码既不现实也无法随口径更新。部署方用 `FOXCLAW_CONTENT_LEXICON` 挂载外部词表（逗号分隔多个文件，一行一词，可选 `词<TAB>类别`）；`tools/build_content_lexicon.py` 负责格式转换（自动判别编码、按类别筛选、按文件名推类别、转换后回读验证），挂哪几类由部署方自己定。**这一层不挂就不生效**：默认只有内置规则在跑。
- `FOXCLAW_CONTENT_GUARD_MODE` 三档：`enforce`（默认）、`audit`（只记录）、`off`。默认强制，因为这是给对外产品兜底的机制，默认不生效等于没做；内置规则范围窄，正常对话不会被误伤。
- 归一化对抗全角、零宽字符、空格插字。拼音、谐音、拆字改写不在确定性层处理 —— 这里明确只做限速带，不做墙。
- 知识库导入与附件正文**不过滤**：那是用户自己的资料。过滤器只挂在「用户请求」和「模型输出」两端，避免把历史书和法律条文当成违规请求。
- 行为规范新增 `no_harmful_content`（`enforce: both`），`KERNEL_PROMPT` 增加【八、内容边界】写明拒绝范式，并区分「资料」与「指令」两种角色。
- 新增 `docs/CONTENT_GUARD_CN.md` / `docs/CONTENT_GUARD.md`：挂载方式、词表格式、格式转换与误伤调优。仓库不附带任何政治 / 色情 / 暴力词表，是否挂载、跑哪一档由部署方决定。

**Agent**

- Agent 启动时会把 `foxclaw/agents/rules/<AGENT_ID>/*.md` 读进系统提示词。每次组装提示词都重新读盘，改规则不必改代码；该段附在角色职责之后，与上游安全规则冲突时以安全规则为准。设 `FOXCLAW_AGENT_RULES=0` 可整体关闭。
- 随包附带 `foxclaw/agents/rules/finance/RULES.md` —— Personal Finance Agent Rules v1.0（数据真实性、账目准确性、隐私、计算口径、建议边界、异常处理、记忆边界、与主管协作）。该文件属 package data，随 wheel 一起分发，不会像仓库根的 `skills/` 那样漏包。

**知识**

- 新增 `POST /conflicts/{id}/dismiss` 与 `POST /conflicts/bulk-dismiss`（SDK：`dismiss_conflict` / `dismiss_conflicts`），关闭卡片但不判胜负：双方 claim 保持 `active`，没有一方被标成 `superseded`。仅因该卡被置为 `disputed` 的观点，在不牵涉任何 open 冲突后恢复为 `active`。
- 冲突判定不再只凭主题相似就翻转立场：否定侧还须与另一句字面重合（字符二元组 Jaccard ≥ 0.40）且语义相近（余弦 ≥ 0.35），两个阈值均可由构造参数（`neg_flip_min_cos` / `neg_flip_min_overlap`）调整。
- 「不变」「不同」「不管」「不再」「无需」「不可观测」「不可控」这类描述性「不 / 无」不再算作立场否定。
- 冲突卡新增次按钮「误判」，与「采纳所选观点」并列。
- 新增 `tools/recheck_conflicts.py`：用当前规则复算存量 open 卡并批量关闭已不成立的，默认 dry-run，`--apply` 经运行中的服务提交，保证进程内冲突集合与向量索引同步。

## 0.1.3 — 2026-11-11

界面双语化，并把「设置」放到该在的位置。

**界面**

- 新增 i18n 基建（`lib/i18n.js` + 中英字典），侧栏、菜单、对话、舞台、设置、提示文案统一走字典；缺词回退 key，不会渲染成空白。
- 设置面板新增「界面 → 语言」，中文 / English 即时切换，写入 `localStorage`（`foxclawLang`），刷新后保持。
- 日期与数字格式随语言切换（`zh-CN` / `en-US`），`<html lang>` 同步。
- 「设置」从「更多」弹层移到左栏底部常驻入口，带分隔线，不随会话列表滚动。
- 顶栏版本号改为读后端 `/api/system/info`，取不到时不再显示硬编码的 `v3.0`。

**文档**

- README 按仓库实际校准：智能体数量、渠道、摄取上限、分块几何、支持格式、内部链接全部逐条核对，并加测试防止再次漂移。

## 0.1.2 — 2026-11-10

版本号收口、技能安装成型。

- 版本号五处统一（`pyproject.toml` / `foxclaw/__init__.py` / `npm/package.json` / `npm/index.js` / `frontend_src/package.json`），并由 `tests/test_version_sync.py` 盯住，防 npm 与 PyPI 漂移。
- `foxclaw skills install` 支持 ClawHub / GitHub / URL / 本地包；工具面板可直接上传 `SKILL.md` 或 OpenClaw 包安装指令技能。
- 首次启动引导询问称呼，可在设置中修改；新增可选的新版本检查提示。
- `tools/sync_public.ps1` 改为直接构造公开树推远端，不再依赖本地克隆。
- 类型标注收口：mypy 从 645 降到 0，CI 改为棘轮门（只降不升）。
- 全仓剥离解释性注释，公开树下不再混入内部说明。

## 0.1.1 — 2026-11-09

只在 npm 通道补发，仓库内没有对应版本号的提交记录。

- npm 包排除 `docs/reports` 等内部文档与调试残留，自检命中即中止打包（0.1.1 曾把内部文档打进公开包）。
- `npm pack` 前先跑 `build.cjs`，修 GitHub Release 附件只有 2KB 空壳。
- 修 `pyproject` 缺 `readme`，PyPI 项目页不再没有项目描述。
- `requirements.txt` 改用 `uv pip compile --universal`，平台相关依赖带上 marker；CI 增加依赖审计门。
- 补 MIT 许可证与 metadata。

## 0.1.0 — 2026-11-06

首个公开版本，PyPI `foxclaw` 与 npm `foxclaw` 双通道。

- 运行时：FastAPI 服务 + `foxclaw` 命令行入口。
- 智能体：多智能体收敛为三（助手 / 办公 / 知识），其余能力下放为指令技能。
- 知识库（HKC）与记忆（HMR）以内嵌 `vendor/` 形式接入：知识可抽取、可检索，复习走 SM-2 排程。
- 治理层：策略门、审计哈希链、影子学习与断路器；导入路径白名单，技能副作用默认 fail-closed。
- 配置：从 `.env` 改为类型化读取，`foxclaw config check` 把「配置写错」从崩溃变成提示。
- 安全：堵 DNS rebinding、浏览器 SSRF、渠道陌生指令三个可利用面；渠道路径签名 + 动作一次性 nonce 确认。
- 链路：计划可见化、推理过程独立 SSE 事件、步骤级 checkpoint 断点续跑。
- 前端：Vue3 三栏渲染层，左栏功能入口常驻，右栏双态检视。

> 两个包都是**占位包**：`vendor/`（HKC / HMR）不入包，装完只能 import 骨架，完整运行时从仓库获取。详见 [docs/RELEASE.md](docs/RELEASE.md)。
