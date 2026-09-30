# Hestia OS · 认知智能体操作系统

**认知 Agent 操作系统** —— 以 HMR 记忆运行时为内核,五个智能体协同,Harness 跨层治理,记忆/认知层与 LLM 彻底解耦。

```
传统智能体框架:  记忆 = 存储,        智能体 = 无状态执行
Hestia OS:        记忆 = 持续认知运行时,智能体 = 会推理 · 会学习 · 会演化
```

## 一、这是什么

Hestia OS 不是"又一个智能体框架",而是智能体的操作系统。它建立在 HMR(Hestia 记忆运行时)之上,核心护城河独立于任何大模型:

- **推理链(ThoughtChain)** —— 每个智能体的推理轨迹是可审计、可对比的有向图,不是普通日志
- **线束(Harness)** —— 跨层约束与校准:三维漂移检测(语义/工具/范围)+ 影子模式自校准 + 策略自学习
- **演化(Evolution)** —— 每次线束截断都让策略学习,系统越用越准
- **运行时治理** —— 幂等、崩溃恢复、循环检测等,保证系统在真实运行中可靠、可审计、不丢不重

五个智能体:主管(协调)、助手(协助)、财务(理财)、行政(行程)、办公(学习)。每个拥有独立记忆与策略演化,通过 Skill 接入层挂载外部生态,通过渠道层接入主流消息平台。

## 二、五个智能体与领域内核

### 领域专家(各挂一个领域内核)

财务、办公、行政三个智能体各挂一个**同构**的领域内核(纯算法层 + 外部工具接口 + 守门人):

- **财务内核**:预算推理、MAD 异常检测、订阅膨胀、现金流预测;守门人四权限(veto/freeze/确认/alert),只产出审批请求不自动动钱。**前沿三件套**:结构化证据链(可解释风险)、财务数据图(追溯"谁造成超支")、对账(三级匹配 + 疑似盗刷识别)。
- **学习内核**:SM-2 间隔重复(掌握度 + 遗忘风险)+ 知识图谱(知识簇发现)。
- **行程内核**:TSP 路线优化、可达性"赶不及"检测、时间冲突、地理聚类;接高德地图。

### 协调 / 通才

- **主管智能体**:全局态势 + 跨智能体编排。综合决策路由,支持多 Agent 顺序执行(如"先查日程再分析理财"→ admin + finance),跨智能体守门人裁决。
- **助手智能体**:Skill 接入层(本地脚本 / MCP / Web API 三类),有副作用的 skill 需确认后执行。

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
hestia_os/
├── README.md / README_CN.md      项目总览(英 / 中)
├── pyproject.toml
├── hestia/                       后端 Python 包
│   ├── __init__.py               HestiaOS 系统入口(装配所有子系统)
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
├── tests/                        35 个测试文件 + run_all.py
└── docs/                         RUN / CHANNELS / GOVERNANCE / 配置指南(公开)
```

## 六、环境要求

- **Python 3.11 及以上**(3.12 最稳定)
- ⚠ **Windows 用户**:不要把项目放在 OneDrive 接管的目录(文档/桌面/图片),Python 无法在重定向占位符里建目录。放到 `C:\hestia_os` 这类简单路径。

## 七、快速开始

```bash
# 1. 安装依赖
pip install fastapi uvicorn "httpx<0.28" openai cryptography websockets pyyaml numpy scikit-learn rich pydantic
#    (Windows 上 pip 装系统包可能需要 --break-system-packages)

# 2. 可选:更精确的语义检测(约 800MB,不装则降级 n-gram)
pip install sentence-transformers

# 3. 验证认知运行时(无需 API key)
python tests/run_all.py        # 全量回归,应 35/35 通过

# 4. 启动服务(配 key 则真实调模型,不配则 stub)
export DEEPSEEK_API_KEY=sk-...  # 或 ANTHROPIC_API_KEY;Windows: $env:DEEPSEEK_API_KEY="sk-..."
python -m uvicorn hestia.api:app --port 8000
```

详见 [docs/RUN_CN.md](docs/RUN_CN.md)。

## 八、双层记忆架构(私有隔离 + 共享协调)

```
私有层(各智能体隔离,agent:<id> 标签)
  └─ 财务的私人理财偏好 → 办公智能体看不到 ✓
共享层(shared 标签,所有智能体可召回)
  └─ 主管的调度决策记录 → 所有智能体都能看到 ✓
```

加了共享不等于打破隔离:其他智能体能召回主管的调度记录(协调),但拿不到财务的私有记忆(隔离)。

## 九、架构哲学

记忆与认知层和大模型彻底解耦。大模型只是可替换的文本生成器,真正的护城河(推理链审计 + 线束漂移检测 + 策略自学 + 领域内核)独立于任何模型而存在。这是品类竞争,不是功能竞争。
