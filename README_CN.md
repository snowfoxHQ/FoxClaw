# Foxclaw · 有记忆的个人智能体

**有记忆的个人智能体** —— 表面上是有记忆的智能体，往里看是智能体运行时：以 HMR 记忆运行时为内核,三个智能体协同,Harness 跨层治理,记忆/认知层与 LLM 彻底解耦。

```
传统智能体框架:  记忆 = 存储,        智能体 = 无状态执行
Foxclaw:        记忆 = 持续认知运行时,智能体 = 会推理 · 会学习 · 会演化
```

## 一、这是什么

Foxclaw 不是"又一个智能体框架",而是一个真正记得住事的个人智能体；技术上它是一个智能体运行时。它建立在 HMR(Foxclaw 记忆运行时)之上,核心护城河独立于任何大模型:

- **推理链(ThoughtChain)** —— 每个智能体的推理轨迹是可审计、可对比的有向图,不是普通日志
- **线束(Harness)** —— 跨层约束与校准:三维漂移检测(语义/工具/范围)+ 影子模式自校准 + 策略自学习
- **演化(Evolution)** —— 每次线束截断都让策略学习,系统越用越准
- **运行时治理** —— 幂等、崩溃恢复、循环检测等,保证系统在真实运行中可靠、可审计、不丢不重

三个智能体:主管(协调)、财务(理财)、办公学习(学习 + 日程行程)。每个拥有独立记忆与策略演化,通过 Skill 接入层挂载外部生态,通过渠道层接入主流消息平台。原「助手」「行政」已**下放为指令技能**(`skills/general-assistant`、`skills/schedule-itinerary`),按需挂载,不再单列 Agent。

## 二、三个智能体与领域内核

### 领域专家(各挂一个领域内核)

财务、办公学习两个智能体各挂一个**同构**的领域内核(纯算法层 + 外部工具接口 + 守门人);办公学习同时挂学习与行程两个内核:

- **财务内核**:预算推理、MAD 异常检测、订阅膨胀、现金流预测;守门人四权限(veto/freeze/确认/alert),只产出审批请求不自动动钱。**前沿三件套**:结构化证据链(可解释风险)、财务数据图(追溯"谁造成超支")、对账(三级匹配 + 疑似盗刷识别)。
- **学习内核**:SM-2 间隔重复(掌握度 + 遗忘风险)+ 知识图谱(知识簇发现)。
- **行程内核**:TSP 路线优化、可达性"赶不及"检测、时间冲突、地理聚类;接高德地图。

### 协调 / 技能

- **主管智能体**:全局态势 + 跨智能体编排。综合决策路由,支持多 Agent 顺序执行(如"先查日程再分析理财"→ office + finance),跨智能体守门人裁决;无领域关键词命中时自行处理(不再有兜底 Agent)。
- **指令技能**:Skill 接入层(本地脚本 / MCP / Web API 三类)对所有 Agent 开放,有副作用的 skill 需确认后执行。

## 三、消息平台接入

通过统一的渠道抽象层接入四个主流平台,**每个适配器按各自官方规范实现**(不套一个模子):

| 渠道 | 接入模型 | 凭证 |
|------|---------|------|
| 微信 | iLink Bot API,长轮询 + context_token | 扫码登录 token |
| 飞书 | 事件订阅 webhook + 签名验签 + token 刷新 | App ID/Secret |
| Telegram | Bot API getUpdates 轮询 | bot token |
| Discord | Gateway WebSocket + REST | bot token |

闭环调度(`ChannelDispatcher`):收消息 → 主管处理 → 回复原渠道,跨渠道会话隔离 + 幂等去重。详见 [docs/CHANNELS.md](docs/CHANNELS.md)。

## 四、运行时治理

保证系统在真实运行(消息重发、进程崩溃、长任务、死循环)中可靠。详见 [docs/GOVERNANCE.md](docs/GOVERNANCE.md):

- **幂等守卫** —— 消息重发只处理一次(已接入渠道闭环)
- **崩溃/重启恢复** —— 启动时识别上次被中断的任务并诚实交代(boot_id 防 pid 复用)
- **工具循环检测** —— 检测重复/横跳/轮询卡死,防无进展死循环烧 token
- **上下文压缩** —— 长对话渐进降级压缩,保留关键标识符
- **钩子标准化** —— 标准生命周期钩子 + 明确阻断权
- **干预队列** —— 租约机制安全回收子任务结果(为并行子 Agent 预留)

## 五、目录结构

```
foxclaw/
├── README.md / README_CN.md      项目总览(英 / 中)
├── pyproject.toml
├── foxclaw/                       后端 Python 包
│   ├── __init__.py               Foxclaw 系统入口(装配所有子系统)
│   ├── api.py                    FastAPI + WebSocket
│   ├── core/                     thought_chain / memory_adapter / model_router /
│   │                             system_bus / truth_pipeline / token_meter /
│   │                             web_search / compaction / idempotency / recovery
│   ├── kernel/                   运行时内核:调度 / 事件 / 能力 / 生命周期 / 准入
│   ├── pipeline/ planning/       任务执行管线 + 规划原语
│   ├── cognitive/ knowledge/     领域推理 + 知识接入
│   ├── agents/                   base.py(统一 run_task 模板) + agents.py(五智能体)
│   ├── harness/                  harness.py(三维漂移) + loop_detection.py
│   ├── finance/                  财务内核 + 三件套(evidence/graph/reconcile)
│   ├── learning/                 学习内核(SM-2 + 知识图谱)
│   ├── itinerary/                行程内核(TSP + 高德)
│   ├── channels/                 渠道抽象层 + 微信/飞书/Telegram/Discord + 闭环调度
│   ├── skills/                   Skill 接入层 + 权限(permission)/钩子/断路器/循环防护
│   ├── search/                   HSR 认知搜索(HMR 记忆 + HKC 知识 + 联网,融入)
│   ├── browser/                  HIR 浏览器子系统(AI 用的浏览器,融入)
│   ├── orchestration/            跨 Agent 编排(P3 已去领域硬编码,通用)
│   ├── capability/ governance/   embedding 单例 / 治理中间件
├── vendor/hmr/ vendor/hkc/       HMR v2.0 记忆运行时 + HKC 知识核心(已内嵌)
├── examples/                     样例数据
├── tests/                        76 个测试文件(全部 pytest)+ run_all.py
└── docs/                         RUN / CHANNELS / GOVERNANCE / 配置指南(公开)
```

## 六、环境要求

- **Python 3.11 及以上**(3.12 最稳定)
- ⚠ **Windows 用户**:不要把项目放在 OneDrive 接管的目录(文档/桌面/图片),Python 无法在重定向占位符里建目录。放到 `C:\foxclaw` 这类简单路径。

## 七、快速开始

```bash
# 1. 建隔离环境 —— 不要直接装进系统 Python,否则会和机器上其它项目互相污染
python -m venv .venv
.venv\Scripts\activate            # macOS / Linux: source .venv/bin/activate

# 2. 安装锁定版本的依赖(77 个包,含 torch / faiss,约 1GB)
pip install -r requirements.txt
pip install -e . --no-deps        # 装 foxclaw 本体,不再重解析依赖

# 3. 验证认知运行时(无需 API key)
python tests/run_all.py        # 全量回归(全绿)

# 4. 启动服务(配 key 则真实调模型,不配则 stub)
export DEEPSEEK_API_KEY=sk-...  # 或 ANTHROPIC_API_KEY;Windows: $env:DEEPSEEK_API_KEY="sk-..."
python -m uvicorn foxclaw.api:app --port 8000
```

> `requirements.txt` 由 `uv pip compile pyproject.toml --universal -o requirements.txt` 从 `pyproject.toml` 生成,
> **不要手改**。`--universal` 不能省:不加的话解析结果只针对当前平台,会把 Windows 专属包
> (如 `pywin32`)写成无条件依赖,Linux CI 直接装不上。加了它平台相关的包才带
> `; sys_platform == ...` 标记,pip 会自动跳过不适用项。
> 改完 `pyproject.toml` 里的依赖后重跑一次,否则锁定清单会滞后于声明,
> SCA 扫描和 CI 装的都会是旧版本。

详见 [docs/RUN_CN.md](docs/RUN_CN.md)。

### 开发常用命令(Windows / PowerShell 一行重启)

开发期改完 `.env`、装了新包、调了后端代码,都要重启 foxclaw 让改动生效。**最简一行函数**,配一次永远省事:

```powershell
# ① 打开 PowerShell 启动脚本(只首次)
notepad $PROFILE

# ② 文件末尾粘这段,保存退出
function restart-foxclaw {
  $p=(Get-NetTCPConnection -LocalPort 8000 -State Listen -EA SilentlyContinue).OwningProcess
  if($p){Stop-Process -Id $p -Force; Start-Sleep 1}
  Set-Location <你的项目目录>
  # 显式走 .venv,别用 PATH 里那个系统 Python 的 foxclaw.exe ——
  # 系统环境常常是很久以前装的,挂着已知漏洞(实测 278 条)。
  .\.venv\Scripts\foxclaw.exe serve
}

# ③ 重开 PowerShell 让配置生效(只在改了 $PROFILE 后需要)
exit
```

之后**任何目录、任何时候**,一行搞定:

```powershell
restart-foxclaw   # 自动杀旧进程 → 切项目根 → 起新服务
```

如果 `notepad $PROFILE` 报"无法加载脚本",先放宽执行策略:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

| 场景 | 命令 |
|---|---|
| 改完 `.env` 要生效 | `restart-foxclaw` |
| 装了新 pip 包 | `restart-foxclaw` |
| 改完 `foxclaw/*.py` | `restart-foxclaw` |
| 只想杀旧实例 | `$p=(Get-NetTCPConnection -LocalPort 8000 -State Listen -EA SilentlyContinue).OwningProcess; if($p){Stop-Process -Id $p -Force}` |
| 想边改边自动重启 | `python -m uvicorn foxclaw.api:app --port 8000 --reload`(开发期才用,生产关掉) |

### 方式二 · Docker 部署

```bash
# 构建镜像(含依赖 + 默认 embedding 预热,首次构建较慢、镜像较大)
docker build -t foxclaw .

# 启动服务(密钥经 --env-file 注入,勿写进镜像)
docker run -d --name foxclaw \
  -p 8000:8000 \
  --env-file .env \
  -v foxclaw_data:/app/foxclaw_data \
  foxclaw

# 容器内跑全量回归(76 项,验证镜像健康)
docker run --rm foxclaw python tests/run_all.py
```

要点:

- 默认命令为 `uvicorn foxclaw.api:app`,监听 `8000`;未配 `FOXCLAW_API_KEY` 时无鉴权,仅限回环安全。
- 数据目录 `FOXCLAW_DATA_DIR`(默认 `./foxclaw_data`)建议挂 volume 持久化。
- 密钥经 `--env-file .env` 或 `-e FOXCLAW_API_KEY=...` 注入;`.dockerignore` 已排除 `.env`,不会被打进镜像。

## 八、知识库(HKC)模型配置

HKC 知识库用**两个不同类型的模型**各司其职,都在 `.env` 里配置,无需改代码:

| 环节 | 模型类型 | 作用 | 配置项 |
|------|---------|------|--------|
| 写入(摄入书籍/文件时) | **生成式 LLM**(会"读懂"并输出 JSON) | 把每个分段抽成实体/概念/事实/观点 | `HKC_LLM_*` |
| 检索(查询时) | **embedding**(文本 → 向量) | 把知识与查询向量化,算语义相似度召回 | `HKC_EMBEDDING_*` |

两者不能互换:embedding 模型不会生成文本(抽不出 JSON),LLM 也不适合做向量检索。

### 先确定你属于哪种情况

| 你的环境 | 抽取模型怎么配 | `HKC_LLM_API_KEY` 填什么 |
|---|---|---|
| 有本地 Ollama | `openai-compatible` + `base_url` 指向 Ollama | **任意非空**即可(Ollama 不校验,填 `ollama` 只是占位) |
| 无本地模型 | 用**云端公开模型**(DeepSeek / OpenAI / 通义等),见下表 | 填**对应平台的真实 API Key** |

### 情况 A · 本地 Ollama

```bash
HKC_LLM_PROVIDER=openai-compatible
HKC_LLM_API_KEY=ollama                       # 仅本地 Ollama 可这样占位;云端不能这么填
HKC_LLM_MODEL=my-qwen-32k:latest             # 你 `ollama list` 里的模型名
HKC_LLM_BASE_URL=http://localhost:11434/v1   # Ollama 的 OpenAI 兼容端点
HKC_LLM_TIMEOUT=180                          # 本地模型慢,超时放宽(默认 60 秒)
HKC_EXTRACT_CONCURRENCY=2                    # 本地单卡 1~2;云端 API 可 5
HKC_EXTRACT_BATCH=8                          # 批量抽取(可选):一次调用抽 N 段,省 token、提速;默认 1=逐段
HKC_MAX_CHUNKS_PER_DOC=800                   # 单文档 chunk 上限:默认 800 段(约 125 万字符);0=不限制,详见下文「单文档分段上限」
```

### 情况 B · 云端公开模型(无本地模型时)

`HKC_LLM_MODEL` 填该平台的**公开模型号**(OpenAI 兼容接口透传任意字符串);`HKC_LLM_API_KEY` 填对应平台的**真实密钥**:

| 平台 | `HKC_LLM_PROVIDER` | `HKC_LLM_API_KEY` | `HKC_LLM_MODEL` 示例 | `HKC_LLM_BASE_URL` |
|---|---|---|---|---|
| DeepSeek | `deepseek` | `sk-...`(DeepSeek 官方 key) | `deepseek-chat` / `deepseek-reasoner` | 留空(自动用官方地址) |
| OpenAI | `openai` | `sk-...`(OpenAI key) | `gpt-4o-mini` / `gpt-4o` | 留空 |
| 通义千问 | `openai-compatible` | 阿里云 DashScope key | `qwen-max` / `qwen-plus` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 智谱 / 月之暗面 / MiniMax 等 | `openai-compatible` | 该平台 key | 该平台公开模型号 | 该平台 OpenAI 兼容地址 |

示例(DeepSeek):

```bash
HKC_LLM_PROVIDER=deepseek
HKC_LLM_API_KEY=sk-你的DeepSeek密钥
HKC_LLM_MODEL=deepseek-chat
```

### 检索模型(embedding)

检索模型**不走 Ollama**,而是 sentence-transformers;本地没有权重时会自动从 HuggingFace 下载:

```bash
HKC_EMBEDDING=local
HKC_EMBEDDING_MODEL=BAAI/bge-m3          # 首次自动下载(约 2.2GB);或填本地路径 C:\...\bge-m3
```

不追求语义精度时可不配(默认 `stub` 伪语义,开箱能跑)。

### key 在哪填、优先级

- **`.env` 是权威来源** —— 启动时读取,改完重启生效。
- 星球页「连接设置」里的「知识抽取模型」段是**可选的运行时覆盖**(存浏览器 localStorage,刷新可能覆盖 .env);想始终以 .env 为准,那段留空即可。
- 页面第一个「API Key」是访问后端的鉴权通行证(= `.env` 的 `FOXCLAW_API_KEY`),与模型 key 无关。

> 提示:换 embedding 模型会改变向量维度(如 384→1024),需重建知识库索引(清空后重新摄入)。

### 一次能摄入多少内容

**上限是硬性的,超了直接报错,不会静默截断:**

| 入口 | 上限 | 超限表现 |
|------|------|----------|
| 上传文件(`/knowledge/ingest/file`、`/ingest/file/async`) | **20 MB / 个文件** | `413 文件过大` |
| 粘贴文本(`/knowledge/ingest/text`) | **2,000,000 字符**(中文约合 200 万字 / 6 MB) | `413 文本过大` |
| 单文档分段数 | **默认 800 段**(约 125 万字符);`HKC_MAX_CHUNKS_PER_DOC=0` 为不限 | 只处理前 N 段,日志输出「文档过大」警告 |

支持格式:`.pdf` `.md` `.markdown` `.txt` `.epub` `.mobi` `.azw3` `.azw` `.docx` `.xlsx`。`.doc` 不支持;**扫描版 PDF 没有 OCR**,纯图片页面提不出任何文字。

**分块是计费和耗时的单位**,不是可调项:

```
每段上限 1792 字符,相邻重叠 224 字符
→ 每 1568 个字符 ≈ 1 段(1 次 LLM 抽取单元)
```

**耗时与费用参考**(batch=4、并发=8、云端 DeepSeek,中文内容实测口径):

| 中文内容 | 文件大小 | 段数 | 预计耗时 |
|---|---|---|---|
| 10 万字符 | 约 100 KB | 约 64 段 | 约 6 分钟 |
| 50 万字符 | 约 500 KB | 约 320 段 | 约 32 分钟 |
| 100 万字符 | 约 1 MB | 约 640 段 | 约 1 小时 |
| 700 万字符(上限) | 约 20 MB | 约 4400 段 | 约 7 小时 |

token 换算率:**每 1 万字符 ≈ 4 千输入 + 2 千输出 token**(批量抽取已摊薄系统提示),乘你所用平台的单价即可估费。英文按字符计是中文的 2–3 倍,耗时同比例增加。

**实践建议:**

- **单文件控制在 2–5 MB**。一本普通电子书就在这个区间,几十分钟内能跑完,中途可随时看进度。
- **`HKC_MAX_CHUNKS_PER_DOC` 保持默认 800。** 放宽到 0 之前先读下面那节,理由不只是"慢"。
- **超长内容用文件上传,不要用粘贴。** `/ingest/text` 是同步接口,大文本会一直占着请求;`/ingest/file/async` 才在后台线程跑,可轮询 `processed/total` 看真进度。
- **重复文件不收钱。** 上传前先算内容 SHA-256,同内容再传直接 `409 该文件内容已摄入过`。
- **抽取失败先看日志关键词。** 出现「JSON 解析失败」说明模型输出写到一半被截断(中文密集文档常见),调小 `HKC_EXTRACT_BATCH` 或调大 `HKC_EXTRACT_MAX_TOKENS`(默认 8192,即输出硬顶);没有这个关键词才去查 API Key / 网络。

### 单文档分段上限:为什么默认是 800

`HKC_MAX_CHUNKS_PER_DOC` 默认 **800 段**,不是随手取的数,而是"能完整吞下常见文档"和"跑得完、账单可控"的交点:

| 上限取值 | 覆盖字符 | 段数 | 预计耗时 | 成本(云端) |
|---|---|---|---|---|
| **800(默认)** | 约 125 万 | 800 | 约 1.3 小时 | 约 3 元 |
| 2000 | 约 313 万 | 2000 | 约 3.3 小时 | 约 8 元 |
| 0(不限制,即吃满 20 MB) | 约 700 万 | 约 4400 | **7 小时起步** | 约 17 元 |

> 成本按 `deepseek-chat` 公开单价粗估(输入 2 元 / 输出 8 元 每百万 token),仅作量级参考;换平台按你的价目表折算。

**为什么不能设得太大(尤其别设 0):** 上传侧允许 20 MB,若分段不设防,一次误传(比如一份 20 MB 的扫描版 PDF 或一份日志转存)就会在后台连续跑大半天,并在这段时间里持续产生费用。摄入是后台线程执行的,发起后既没有断点也没有回退按钮,你只能看着它跑完或者杀掉整个服务。默认值的作用是"先拦住明显不合理的量",而不是"用得越少越安全"。

**为什么也不该设得太小:** 超出上限时只处理前 N 段,而**整文件的内容 SHA-256 已经入库**,同一个文件重新上传会被 `409` 去重拦下。也就是说截断之后不能靠"改大上限再传一次"来补齐——必须先把文件按章节拆成几份,分别上传。所以 800 段留了足够余量:绝大多数单篇文档和普通电子书都不会触发截断。

**真要整本摄入时**再针对单次操作临时调大(或设 0),跑完改回 800。日志里出现「文档过大:切出 N 个 chunk」就说明触发了截断,那条警告里会写明本次丢掉了多少内容。

## 九、双层记忆架构(私有隔离 + 共享协调)

```
私有层(各智能体隔离,agent:<id> 标签)
  └─ 财务的私人理财偏好 → 办公智能体看不到 ✓
共享层(shared 标签,所有智能体可召回)
  └─ 主管的调度决策记录 → 所有智能体都能看到 ✓
```

加了共享不等于打破隔离:其他智能体能召回主管的调度记录(协调),但拿不到财务的私有记忆(隔离)。

## 十、架构哲学

记忆与认知层和大模型彻底解耦。大模型只是可替换的文本生成器,真正的护城河(推理链审计 + 线束漂移检测 + 策略自学 + 领域内核)独立于任何模型而存在。这是品类竞争,不是功能竞争。

## 十一、许可证

MIT,全文见 [LICENSE](LICENSE)。一句话:随便用、随便改、随便卖,保留版权声明即可。

`vendor/` 下内嵌的运行时保留各自的许可证(见 `vendor/hkc/LICENSE`)。
