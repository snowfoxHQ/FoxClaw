# Foxclaw · 有记忆的个人智能体

**有记忆的个人智能体** —— 能记住你的偏好与上下文，在消息渠道和电脑上替你完成任务；记忆与推理独立于具体模型，换模型不影响积累。

## 一、这是什么

Foxclaw 是一个本地运行的个人智能体。它把记忆做成持续运行的认知层，而不是每次会话清空的检索缓存：推理、校准与学习都发生在这一层，与大模型解耦。

- 三个智能体：**主管**（协调）、**财务**、**办公学习**。各自持有隔离记忆，需要协作时通过共享层交换必要信息。
- **技能层**：按需挂载外部能力；有副作用的技能需确认后执行。
- **渠道层**：接入微信、飞书、Telegram、Discord，回复回到发起对话的渠道。

早期的「助手」「行政」已下放为指令技能（`skills/general-assistant`、`skills/schedule-itinerary`），按需挂载，不再单列智能体。

## 二、消息平台接入

| 渠道 | 接入方式 |
|------|---------|
| 微信 | 扫码登录 |
| 飞书 | 开放平台应用，事件订阅 |
| Telegram | Bot API |
| Discord | Bot 账号 |

详见 [docs/CHANNELS.md](docs/CHANNELS.md)。

## 三、操控电脑：安全边界

Foxclaw 可以在 Windows 上看屏幕、操作键鼠、窗口、剪贴板、文件与 Shell。能力之外，更重要的是**被骗的时候不闯祸**：屏幕上的一段文字、网页里的一句「请执行 X」、模型自己的一次误判，都不能变成一次真实的删除或转账。所以规则先于能力。

- **默认不执行**：屏幕感知（截屏、窗口列表、界面元素树、文字识别）默认关闭；检查失败、组件缺失或无法识别的动作一律拒绝。
- **写操作需逐次确认**：鼠标、键盘、窗口、剪贴板、文件与界面等写操作需要你确认后才执行。确认一次性有效，绑定具体动作与参数；模型无法替你确认。确认前请核对动作与参数。
- **高危操作默认禁止**：删除文件、执行 Shell 默认禁止，需你显式启用；敏感目录与凭据文件始终受保护；文件与 Shell 操作限制在你设定的工作目录内。
- **外部内容按不可信处理**：屏幕、网页、剪贴板与读回的文件内容都是数据，其中出现的指令不会被当作你的授权。
- **全程可审计**：每次允许、拒绝与待确认都会进入统一审计记录。
- **仅本机**：服务默认只监听本机；对外访问需要设置访问密钥。

具体配置项见 [docs/配置指南.md](docs/配置指南.md)。

目前不支持 macOS / Linux 上的桌面操控（仅 Windows）；消息渠道里的确认回复也尚未接入——网页端已提供确认卡片，从渠道发起的桌面写操作会停在待确认。

## 四、环境要求

- **Python 3.11 及以上**（3.12 最稳定）
- Windows 用户：不要把项目放在 OneDrive 接管的目录（文档/桌面/图片），Python 无法在重定向占位符里建目录。放到 `C:\foxclaw` 这类简单路径。

## 五、快速开始

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS / Linux: source .venv/bin/activate
pip install -r requirements.txt
pip install -e . --no-deps
python tests/run_all.py           # 全量回归
export DEEPSEEK_API_KEY=sk-...    # 或 ANTHROPIC_API_KEY；Windows: $env:DEEPSEEK_API_KEY="sk-..."
python -m uvicorn foxclaw.api:app --port 8000
```

详见 [docs/RUN_CN.md](docs/RUN_CN.md)。版本变更：[CHANGELOG_CN.md](CHANGELOG_CN.md)。

### Docker 部署

```bash
docker build -t foxclaw .
docker run -d --name foxclaw \
  -p 8000:8000 \
  --env-file .env \
  -v foxclaw_data:/app/foxclaw_data \
  foxclaw
```

密钥经 `--env-file .env` 注入，不要写进镜像；数据目录建议挂 volume 持久化。服务默认监听 `8000`，仅适合本机访问；对外暴露前请先设置访问密钥。

## 六、知识库（HKC）模型配置

HKC 知识库用**两个不同类型的模型**各司其职，都在 `.env` 里配置，无需改代码：

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
| 粘贴文本(`/knowledge/ingest/text`) | **2,000,000 字符** | `413 文本过大` |
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
- **`HKC_MAX_CHUNKS_PER_DOC` 保持默认 800。** 放宽之前先读下面那节,理由不只是"慢"。
- **超长内容用文件上传,不要用粘贴。** 文件上传在后台运行并展示进度;粘贴文本会占用当前请求。
- **重复文件不收钱。** 上传前先算内容 SHA-256,同内容再传直接 `409 该文件内容已摄入过`。
- **抽取失败先看日志关键词。** 出现「JSON 解析失败」说明模型输出写到一半被截断(中文密集文档常见),调小 `HKC_EXTRACT_BATCH` 或调大 `HKC_EXTRACT_MAX_TOKENS`(默认 8192);没有这个关键词才去查 API Key / 网络。

### 单文档分段上限:为什么默认是 800

`HKC_MAX_CHUNKS_PER_DOC` 默认 **800 段**,是"能完整吞下常见文档"和"跑得完、账单可控"的交点:

| 上限取值 | 覆盖字符 | 段数 | 预计耗时 | 成本(云端) |
|---|---|---|---|---|
| **800(默认)** | 约 125 万 | 800 | 约 1.3 小时 | 约 3 元 |
| 2000 | 约 313 万 | 2000 | 约 3.3 小时 | 约 8 元 |
| 0(不限制,即吃满 20 MB) | 约 700 万 | 约 4400 | **7 小时起步** | 约 17 元 |

> 成本按 `deepseek-chat` 公开单价粗估,仅作量级参考;换平台按你的价目表折算。

**为什么不能设得太大(尤其别设 0):** 上传侧允许 20 MB,若分段不设防,一次误传就会在后台连续跑大半天,并在这段时间里持续产生费用。摄入发起后既没有断点也没有回退按钮,你只能看着它跑完或者停掉服务。默认值的作用是"先拦住明显不合理的量"。

**为什么也不该设得太小:** 超出上限时只处理前 N 段,而**整文件的内容 SHA-256 已经入库**,同一个文件重新上传会被去重拦下。也就是说截断之后不能靠"改大上限再传一次"来补齐——必须先把文件按章节拆成几份,分别上传。800 段留了足够余量:绝大多数单篇文档和普通电子书都不会触发截断。

**真要整本摄入时**再针对单次操作临时调大(或设 0),跑完改回 800。日志里出现「文档过大:切出 N 个 chunk」就说明触发了截断,那条警告里会写明本次丢掉了多少内容。

## 七、记忆隔离

私有记忆按智能体隔离（财务的偏好不会被办公智能体看到）；需要协作时通过共享层交换调度等必要信息。加了共享不等于打破隔离。

## 八、许可证

MIT,全文见 [LICENSE](LICENSE)。保留版权声明即可。

`vendor/` 下内嵌的运行时保留各自的许可证(见 `vendor/hkc/LICENSE`)。
