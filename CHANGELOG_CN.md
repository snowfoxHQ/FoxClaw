# 更新日志

从 0.1.0 首次公开到当前版本的变更记录，最新的排在最前面。

英文版:[CHANGELOG.md](CHANGELOG.md)。

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
