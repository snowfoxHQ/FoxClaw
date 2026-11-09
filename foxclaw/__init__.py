"""
Foxclaw — 系统入口
统一初始化：HMR + ThoughtChain + Harness + 三个 Agent
"""

import os
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:              # 仅类型检查期:anthropic 在 __init__ 里按需真导入
    import anthropic

# 【跨平台启动修复·统一入口】Windows 控制台默认 GBK(cp936),无法编码 ✓/⚠/· 等诊断字符,
# 打印会抛 UnicodeEncodeError 直接崩溃。典型踩中路径:不经过 api.py 而直接
# `from foxclaw import Foxclaw` 时,memory_adapter 的 "✓ HMR 已接入" 就会崩(且崩在 except 里,
# 连降级 MockHMR 都失败)。在包入口把 stdout/stderr 统一为 UTF-8(errors=replace 兜底),
# 覆盖所有 import foxclaw 的路径。api.py 顶部另有一份同义历史修复,此处为统一入口;幂等无害。
for _stream in (sys.stdout, sys.stderr):
    try:
        # getattr 而非直连:被重定向成 StringIO 等无 reconfigure 的对象时直接跳过。
        _reconf = getattr(_stream, "reconfigure", None)
        if _reconf is not None:
            _reconf(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 版本号单一来源:与 pyproject.toml 保持一致,api.py 的 FastAPI app 版本与
# /api/system/info 都引用这里,避免三处各写一个值再漂移(曾出现 system_info 硬编码
# 1.0.0 而 pyproject/app 是 0.1.0 的不一致)。
__version__ = "0.1.2"

from .core.thought_chain import ThoughtChain

# 五个 Agent 的导入刻意**不放在模块级**,而是放进 Foxclaw.__init__(惰性)。
# 原因:agents→base→harness 会连带拉入 sentence-transformers/torch 等重依赖,而
# `import foxclaw.governance`(治理中间件,只需 harness+thought_chain)不该被迫加载整个
# Agent 框架。惰性化后:仅用治理内核的场景不会拉入 agents/skills/channels/HMR。
# (from foxclaw import Foxclaw 仍照常工作——实例化时才导入 Agent 类。)


class Foxclaw:
    """
    Foxclaw 统一入口
    一次初始化，五个 Agent 共享 HMR + ThoughtChain，各有独立 Harness
    """

    def __init__(
        self,
        data_dir: str = "./foxclaw_data",
        anthropic_api_key: str | None = None,
        shadow_rounds: int = 20,
    ):
        self.data_dir = Path(data_dir).resolve()
        os.makedirs(self.data_dir, exist_ok=True)

        # Anthropic 客户端(保留兼容;模型选择实际由 ModelRouter 统一探测)
        api_key = anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        # 注解放在首赋值前:否则 mypy 以首个分支的 Anthropic 定型,后面的 None 全报错。
        self.client: "anthropic.Anthropic | None" = None
        if api_key:
            try:
                import anthropic
                self.client = anthropic.Anthropic(api_key=api_key)
            except Exception:
                self.client = None
        else:
            self.client = None

        # HMR 认知运行时(通过适配层接入真实 HMR v2.0,失败降级 Mock)
        # 【P1·share-bge-m3】HKC 优先装配,确保 bge-m3 已入 HKC 共享缓存,
        # 然后 HMR 创建时复用同一 SentenceTransformer 实例,启动期不再重复加载。
        # HKC 装配失败(MODEL 缺失/网络问题) → HMR 仍按 env 链回退到 bge-m3 或 MiniLM,
        # 不影响 HMR 可用性。
        try:
            from .knowledge import init_hkc as _init_hkc_early
            _early_hkc = _init_hkc_early(self.data_dir, api_key)
        except Exception as _e:
            _early_hkc = None
            print(f"[Foxclaw] ⚠ 启动期 HKC 预装配失败(继续,降级): {_e}")

        from .core.memory_adapter import create_memory_backend
        # 【P2-fix】create_memory_backend / HMRAdapter 都从 EMBEDDING_MODEL env 读模型名,
        # 加载时调 get_st_model() 走 HKC 进程缓存,bge-m3 instance 共享(只 load 1 次)
        # —— 所以这里**不再**显式传 embedding_model 参数
        self.hmr = create_memory_backend(
            storage_path=str(self.data_dir / "hmr"),
            llm_api_key=api_key,
        )

        # 【P1·share-bge-m3】早期 init 只为预热 bge-m3 入 HKC 共享缓存——绝**不留**容器:
        # 否则 HKCContainer 会被钉在 hkc_api.container._container 全局单例(由 init_hkc
        # line 143 注册)+ HKCService 内部 _container 引用,产生泄漏,在 aclose→rmtree 时
        # 留下 events.db/hkc.db 文件锁(PermissionError [WinError 32])。预热完立即关+清。
        if _early_hkc is not None:
            try:
                _early_hkc.close()
            except Exception:
                pass
            try:
                from hkc_api.container import reset_container
                reset_container()
            except Exception:
                pass
            _early_hkc = None

        # 真正持有 HKCService 的是后续 lazy `self.hkc` 路径(line 374-375)。
        # 此处只置 None/False,与 line 336-342 一致。
        self._hkc_service = None
        self._hkc_inited = False

        # ThoughtChain（共享存储，各 Agent 通过 agent_id 隔离）
        self.thought_chain = ThoughtChain(
            db_path=str(self.data_dir / "thoughtchain.db")
        )

        # 会话存储:让对话有历史(此前无状态,刷新即丢)。文件式、线程安全。
        from .core.sessions import SessionStore
        self.sessions = SessionStore(self.data_dir / "sessions")

        # 多模型路由(共享一个 Router,体现 LLM 解耦;自动探测可用模型)
        from .core.model_router import ModelRouter
        self.router = ModelRouter()

        # Token 计量器(成本可观测性,运行时治理的一块)。挂到 router 上,
        # 所有 LLM 调用经 router.complete() 自动计量,不漏一次。落盘到数据目录。
        from .core.token_meter import TokenMeter
        self.token_meter = TokenMeter(
            log_path=str(self.data_dir / "token_usage.jsonl"))
        self.router.meter = self.token_meter

        # 【P0·统一事件脊柱】全系统唯一的 EventSpine,必须在 Agent 之前建好:
        # 各 Agent 的 Harness 事件、SystemBus 系统事件都汇入这一条脊柱,
        # 由 API 用单一投影统一推给 WS(修复"harness 走桥接、truth/process 到不了前端")。
        from .kernel import EventSpine, CapabilityRegistry, Scheduler
        self.event_spine = EventSpine()

        # 【v2 ①·微内核调度器】任务准入(并发配额)+ 记账 + task.lifecycle.* 事件。
        # 默认无上限(behavior-preserving);FOXCLAW_MAX_CONCURRENCY 设了才背压。线程级(跨事件循环)。
        _maxc = os.environ.get("FOXCLAW_MAX_CONCURRENCY", "").strip()
        self.scheduler = Scheduler(
            max_concurrency=int(_maxc) if _maxc.isdigit() else None,
            spine=self.event_spine)

        # 【P1-2·统一治理审计】governance.decision 事件流 + {data_dir}/audit/governance.jsonl
        # 落盘。须在 PolicyGate/Orchestrator 之前建好(二者都注入它);FOXCLAW_GOVERNANCE_AUDIT=0
        # 时 attach 返回 None,各层按 None 跳过记录(零开销降级)。
        from .core.governance_audit import attach as _attach_gov_audit
        self.governance_auditor = _attach_gov_audit(self.event_spine, self.data_dir)

        # 【v2 ① Step4·统一准入门】把 Harness(推理漂移)与 skill-guard(动作权限)统一到
        # 一套 Verdict 词汇 + 一个接口。evaluate 注入(避免 kernel→skills 环)。
        # 【P3-4】auditor 注入后,每次裁决带 decision_id 并记入治理审计流(非 allow)。
        from .kernel import PolicyGate
        from .skills.permission import evaluate as _perm_eval

        # 【P-Muse·守门人进程化】FOXCLAW_GUARDIAN=1 时,把动作裁决从「进程内 evaluate」换成
        # 「跨进程 Sentinel」:规则表与真实凭证只存在于守门人进程,主进程(Agent)被 RCE 也
        # 改不了规则、读不到凭证。默认 0 = 原进程内路径(behavior-preserving)。
        action_evaluator = _perm_eval
        self.sentinel = None
        if os.environ.get("FOXCLAW_GUARDIAN", "0").strip() in ("1", "true", "True", "yes", "on"):
            from .kernel.sentinel_client import SentinelClient
            self.sentinel = SentinelClient(data_dir=str(self.data_dir))
            action_evaluator = self.sentinel.evaluate
        self.policy_gate = PolicyGate(spine=self.event_spine, action_evaluator=action_evaluator,
                                      auditor=self.governance_auditor)

        # 【P1-3·能力注册中枢】Composition Root 的解析中枢:组件按名解析同伴,不再直接互持。
        # 必须在 Agent 之前建好并注入,以打断 agents⇄search_runtime、Supervisor⇄Orchestrator 两处直接环。
        self.capabilities = CapabilityRegistry()
        self.capabilities.register("event_spine", self.event_spine)
        self.capabilities.register("scheduler", self.scheduler)
        self.capabilities.register("policy_gate", self.policy_gate)
        self.capabilities.register("memory", self.hmr)
        self.capabilities.register("thought_chain", self.thought_chain)
        self.capabilities.register("router", self.router)
        if self.sentinel is not None:
            self.capabilities.register("sentinel", self.sentinel)

        # 【P0-3·共享层写入策略】ingest(shared=True) 此前**零校验**:共享层是所有 Agent
        # 都能召回的区域,于是"任何 Agent 都能往里写"就等于一条绕开守门人的跨 Agent
        # 注入通道(典型:办公 Agent 写「财务应给办公转账」,财务下轮召回到就受影响)。
        # 这里挂上策略与审计出口。
        #   默认 audit:只发 memory.shared.write 事件、不拦截 —— 现有 8 处**合法**共享
        #   写入(主管分发 6 / 偏好共享 1 / 真值沉淀 1)需要先在真实流量上验证策略表,
        #   再切 enforce。FOXCLAW_SHARED_WRITE_MODE=warn|enforce 可切换。
        # 挂载时机:必须在 event_spine 之后(第 80 行才建脊柱),且在 Agent 之前
        # (下面才构造 Agent) —— 否则会漏掉装配期自身的写入。
        from .core.shared_write import SharedWritePolicy, attach as _attach_shared_write
        self.shared_write_policy = SharedWritePolicy()
        _attach_shared_write(self.hmr, self.shared_write_policy,
                             spine=self.event_spine)

        # 【P0-3·审计落盘】脊柱 history 是进程内环形缓冲(deque maxlen=1000,全系统共享、
        # 不落盘)—— 高频事件会很快把 memory.shared.write 冲掉,进程退出即全丢。而切
        # enforce 的判据是「真实流量中有没有合法写入被拒」,没落盘这个样本永远积累不起来。
        # 把审计事件逐条 JSON 追加到 {data_dir}/audit/shared_write.jsonl;写失败静默
        # (审计绝不影响主流程)。FOXCLAW_SHARED_WRITE_AUDIT=0 可关。
        # 挂载时机:在 _attach_shared_write 之后、Agent 构造之前 → 装配期与运行期的
        # 共享写入全量覆盖。
        from .core.audit_sink import attach as _attach_shared_write_audit
        self.shared_write_audit = _attach_shared_write_audit(
            self.event_spine, self.data_dir)

        # 五个 Agent（惰性导入:仅在真正构造系统时才拉入 Agent 框架及其重依赖）
        from .agents.agents import SupervisorAgent, FinanceAgent, OfficeAgent
        agent_kwargs = dict(
            hmr=self.hmr,
            thought_chain=self.thought_chain,
            client=self.client,
            data_dir=str(self.data_dir),
            shadow_rounds=shadow_rounds,
            router=self.router,
            spine=self.event_spine,
            caps=self.capabilities,
        )
        self.supervisor = SupervisorAgent(**agent_kwargs)
        self.finance    = FinanceAgent(**agent_kwargs)
        self.office     = OfficeAgent(**agent_kwargs)

        # Agent 拓扑 5 → 3:assistant/admin 已下放为指令技能(skills/general-assistant、
        # skills/schedule-itinerary),不再常驻;行政的行程内核并入办公学习 Agent。
        self._agents = {
            "supervisor": self.supervisor,
            "finance":    self.finance,
            "office":     self.office,
        }
        self.capabilities.register("agents", self._agents)   # 供 orchestrator/agent_port 惰性解析

        # 给主管注入子 Agent 登记表,使其能真实调度(D 阶段)
        self.supervisor.register_subagents({
            "finance":   self.finance,
            "office":    self.office,
        })

        # 给财务 Agent 挂载财务内核(B 阶段:记忆+推理+Policy 守门人)
        from .finance import FinanceRuntime
        self.finance_runtime = FinanceRuntime(
            policy_path=str(self.data_dir / "finance_policy.yaml"),
            hmr=self.hmr,
        )
        self.finance.attach_runtime(self.finance_runtime)

        # 给办公 Agent 挂载学习内核(B 阶段:间隔重复 + 知识图谱)
        from .learning import LearningRuntime
        self.learning_runtime = LearningRuntime(
            hmr=self.hmr, hkc_provider=lambda: self.hkc,
            persist_path=str(self.data_dir / "learning.json"))
        self.office.attach_runtime(self.learning_runtime)

        # 行程内核(路线优化 + 可达性 + 守门人 + 高德接口)现在挂在办公学习 Agent 上 ——
        # 行政 Agent 下放后,领域内核仍归"离内核最近"的 Agent,编排层不直接读它。
        from .itinerary import ItineraryRuntime
        self.itinerary_runtime = ItineraryRuntime(
            amap_key=os.environ.get("AMAP_MAPS_API_KEY"), hmr=self.hmr)
        self.office.attach_itinerary(self.itinerary_runtime)

        # 给主管挂载编排引擎(全局态势 + 跨 Agent 编排)
        # 【P1-3】编排器经注册中枢惰性解析 agents,不再直接持有含主管的 agents 字典;
        # 裁决经 auditor 记入治理审计流(原本完全无观测)。
        from .orchestration import Orchestrator
        self.orchestrator = Orchestrator(self.capabilities, auditor=self.governance_auditor)
        self.supervisor.attach_orchestrator(self.orchestrator)
        self.capabilities.register("orchestrator", self.orchestrator)

        # 给助手挂载 Skill 接入层(通用工具箱 + 对外生态兼容层)
        from .skills import build_default_registry, SkillAdapter
        self.skill_registry = build_default_registry()
        from .skills.store import SkillStore
        self.skill_store = SkillStore(self.data_dir)
        self.skill_store.load(self.skill_registry)   # 恢复用户安装的技能 + 内置启停覆盖
        # 关键操作(send/pay/destructive)二次确认:HTTP 入口经此签发/校验一次性 nonce。
        # 事件循环里也走同一实例(asyncio.to_thread 复制 contextvars,不含本对象但没关系:
        # broker 自身线程安全)。
        from .core.confirm_nonce import ConfirmationBroker
        self.confirm_broker = ConfirmationBroker()
        self.skill_adapter = SkillAdapter(
            self.skill_registry, mcp_clients={}, policy_gate=self.policy_gate,
            sentinel=self.sentinel, confirms=self.confirm_broker)
        # MCP 连接器(第2期):把连上的 MCP 客户端接到 SkillAdapter.mcp_clients
        from .skills.mcp_client import McpManager
        from .skills.mcp_store import McpStore
        self.mcp_manager = McpManager()
        self.mcp_store = McpStore(self.data_dir)
        for _cfg in self.mcp_store.load():
            self.mcp_manager.restore(_cfg)   # 懒恢复配置,不启动即连
            # 注:磁盘配置里的 headers 只在回退路径(FOXCLAW_GUARDIAN=0)下才存在;
            # 守门人模式落盘的是无 headers 的元数据,真凭证由守门人侧文件持有。
        self.skill_adapter.mcp_clients = self.mcp_manager
        # 【P-Muse·第3步】MCP server 配置(含 headers 凭证)同步给守门人:代执行时守门人持有凭证。
        self.sync_mcp_servers_to_sentinel()
        # 全局技能封杀策略(deny/allow kill-switch):唯一咽喉在 adapter.invoke + md match
        from .skills.policy import SkillPolicy
        self.skill_policy = SkillPolicy(self.data_dir)
        self.skill_adapter.skill_policy = self.skill_policy
        # SKILL.md 指令技能(第3期):挂到全部 agent,注入其提示词
        from .skills.markdown_skill import MarkdownSkillRegistry
        from .skills.markdown_store import MarkdownStore
        self.md_skills = MarkdownSkillRegistry()
        self.md_skills.policy = self.skill_policy
        self.md_store = MarkdownStore(self.data_dir)
        self.md_store.load(self.md_skills)
        # 随包分发的内置指令技能(frontmatter builtin: true):general-assistant /
        # schedule-itinerary —— 原 assistant / admin 两个 Agent 的"新家"。
        # 用户已安装同 id 技能优先,内置只是缺省值;仓库不在(wheel 安装)时静默跳过。
        from .skills.markdown_skill import load_builtin_dir
        load_builtin_dir(self.md_skills, Path(__file__).resolve().parent.parent / "skills")
        for _ag in self._agents.values():
            _ag.md_skills = self.md_skills
            # E2:统一 HookManager(skill_adapter 持有的单例)注入所有 Agent → run_task 的认知管线
            # 能 fire pre_reasoning(agent_start)/task_complete(agent_end)钩子。全 Agent 共享同一套。
            _ag.hooks = self.skill_adapter.hooks
            # 技能工具箱已上移基类:三个 Agent 都能挂载并调用 skill
            _ag.attach_skills(self.skill_registry, self.skill_adapter)
        # 主管也持有 skill 注册表(供 planner 判断任务是否需要拆解)
        self.supervisor.skill_registry = self.skill_registry
        # AHS 执行链编排器(router→planner→composition,全程过 guard+breaker+hooks)
        from .skills.pipeline import AHSPipeline
        self.ahs_pipeline = AHSPipeline(self.skill_registry, self.skill_adapter)
        # 系统事件总线 + 真值管线(执行→审计→记忆 自动流转)
        from .core.system_bus import SystemBus
        from .core.truth_pipeline import TruthPipeline
        # 与 harness 共用同一条脊柱:系统事件与 harness 事件汇流,单一投影统一观测
        self.system_bus = SystemBus(spine=self.event_spine)
        self.truth_pipeline = TruthPipeline(self.system_bus, hmr=self.hmr)
        # 【v2 ④ Step1·真值走 HKC/KEE】叠加路径:truth.appended 时后台把真值经 KDE→KEE 结晶进 HKC
        # (HMR 共享写保留=双写)。默认关(FOXCLAW_TRUTH_TO_HKC=1 才开;KDE 有 LLM 成本)。
        from .knowledge import TruthCrystallizer
        self.truth_crystallizer = TruthCrystallizer(
            hkc_provider=lambda: self.hkc,
            enabled=os.environ.get("FOXCLAW_TRUTH_TO_HKC", "") == "1")
        self.system_bus.subscribe("truth.appended", self.truth_crystallizer.on_truth,
                                  exact=True)   # 【P4-1】精确 channel 名,防前缀事件误入结晶
        # 进程管理器:把每个任务当进程管理(状态机 + 历史)。它是唯一 Lifecycle 后端,
        # emit 唯一词汇 task.lifecycle.<state>(Step3)。
        from .kernel import ProcessManager
        self.process_manager = ProcessManager(bus=self.system_bus)
        # 【Step3】调度器委托 pm 管生命周期:顶层任务经调度器→pm 建进程,与并行分发的子任务
        # 共用同一状态机/同一 task.lifecycle.* 事件流(消除 process.state_changed 与 task.lifecycle 双词汇)。
        self.scheduler.process_manager = self.process_manager
        # 认知状态快照:聚合"系统此刻在想什么"(订阅总线自动更新)
        from .cognitive import CognitiveStateTracker
        self.cognitive_state = CognitiveStateTracker(
            self.system_bus, process_manager=self.process_manager)
        # 把总线和进程管理器交给主管
        if "supervisor" in self._agents:
            self._agents["supervisor"]._system_bus = self.system_bus
            self._agents["supervisor"]._process_manager = self.process_manager

        # 崩溃/重启恢复:启动时扫描上次被中断的任务
        from .core.recovery import RestartRecovery
        self.recovery = RestartRecovery(
            log_path=str(self.data_dir / "task_log.jsonl"),
            spine=self.event_spine)              # 【P2-4】注入审计脊柱(recovery.plan 事件)
        # 恢复记账注入**所有** Agent(不只主管):此前只有主管接线,领域 Agent 跑一半
        # 崩溃在恢复扫描里不可见,也没有步骤级 checkpoint 可供断点续跑复用。
        for _a in self._agents.values():
            _a._recovery = self.recovery
        self.interrupted_tasks = self.recovery.scan_interrupted()
        # 【P2-4】生成恢复计划(retryable/notify_user/needs_manual),供 API 层/前端消费
        self.recovery_plan = self.recovery.plan_recovery(self.interrupted_tasks)

        # 浏览器子系统(HIR-Browser 融入):"AI 用的浏览器"。async 安全 + 可选依赖优雅降级。
        # 构造极轻(不开线程/不 launch chromium),首次 perceive/act 时才在专用工作线程上
        # 惰性开会话;playwright 缺失时 available()=False,路由降级而非崩溃。
        from .browser import BrowserService
        self.browser_service = BrowserService(
            headless=os.environ.get("FOXCLAW_BROWSER_HEADLESS", "1") != "0")

        # HKC 知识核心(vendor/hkc,同进程 DirectClient)。**惰性装配**:首次访问 self.hkc
        # 才装配——否则每次构造 OS 都会在 data_dir/hkc 开 4 个 SQLite 句柄,现有测试
        # rmtree(tmpdir) 时会踩 Windows WinError 32。缺依赖/装配失败时降级 None。
        self._hkc_api_key = api_key
        self._hkc_service = None
        self._hkc_inited = False
        self._hkc_lock = threading.Lock()   # 【F2 修复】护惰性装配,防并发首访双重装配 HKC(句柄泄漏)

        # 认知搜索内核(HSR):统一"查 HMR 记忆 + 查 HKC 知识 + 联网搜索",五个 Agent 共享。
        # 【P1-3】改由能力中枢惰性解析(register_lazy):首次 get 才构造,构造在 _build_search_runtime。
        # Agent 经 self._caps.get("search_runtime") 反向取用,不再被直接注入 _search_runtime(打断直接环)。
        self.capabilities.register_lazy("hkc", lambda: self.hkc)
        self.capabilities.register_lazy("search_runtime", self._build_search_runtime)

        print(f"[Foxclaw] ✓ 系统启动完成，数据目录: {self.data_dir}")
        print(f"[Foxclaw] ✓ 三个 Agent 已就绪: {list(self._agents.keys())}")
        if self.interrupted_tasks:
            print(f"[Foxclaw] ⚠ 检测到 {len(self.interrupted_tasks)} 个上次被中断的任务"
                  f"(进程崩溃/重启)——恢复计划:可重试 {len(self.recovery_plan.retryable)},"
                  f"通知用户 {len(self.recovery_plan.notify_user)},"
                  f"需人工 {len(self.recovery_plan.needs_manual)}：")
            for t in self.interrupted_tasks[:5]:
                print(f"           · 「{t.description[:40]}」(中断于 pid={t.pid})")
        _ri = self.router.info()
        if _ri['active']:
            print(f"[Foxclaw] ✓ 模型路由: {_ri['provider']}（{_ri['model']}）")
        else:
            reason = getattr(self.router, 'fallback_reason', '') or '未配置任何模型 Key'
            print(f"[Foxclaw] ⚠ 模型路由: 探测到 '{_ri['provider']}' 但降级 stub — {reason}")

    @property
    def hkc(self):
        """HKC 知识核心接入句柄(HKCService)或 None(降级)。惰性装配:首次访问才装。
        线程安全(双检锁):并发首访只装配一次,避免多开一套 SQLite 句柄泄漏。"""
        if self._hkc_inited:
            return self._hkc_service
        with self._hkc_lock:
            if not self._hkc_inited:
                from .knowledge import init_hkc, bridge_hkc_events
                self._hkc_service = init_hkc(self.data_dir, self._hkc_api_key)
                # 【HKC 融合·事件面】把 HKC 内部 EventBus 桥接到统一 EventSpine,消除"事件孤岛"
                # (知识演化对 WS/robot/history 可见)。降级/失败静默,不影响 HKC 可用性。
                if self._hkc_service is not None:
                    bridge_hkc_events(self._hkc_service.event_bus, self.event_spine)
                self._hkc_inited = True
        return self._hkc_service

    def sync_abilities_to_skills(self) -> list:
        """【HKC 第2档 Step4】把 HKC 中 coverage 达标的 ACE 能力注册进 SkillRegistry(证据 gate:
        不足不注册)。**opt-in**:不在启动时自动调(编译有图开销、知识随时间变);由调用方在知识
        就绪后按需触发。注册的能力 skill 为 read,经 PolicyGate 治理、可被管线 stage_route 路由。
        返回注册的 skill id 列表。"""
        from .knowledge import register_abilities_as_skills
        reg = getattr(self, "skill_registry", None)
        return register_abilities_as_skills(reg, self.hkc)

    def sync_mcp_servers_to_sentinel(self) -> None:
        """【P-Muse·第4步】把当前 MCP server 元数据同步给守门人(全量)。
        主进程不再长期持有 headers,故这里只推元数据;守门人侧 headers 空则保留旧值,
        add_server 那次由 mcp_add_server_guardian 临时带一次 headers 覆盖。"""
        sent = getattr(self, "sentinel", None)
        if sent is not None and getattr(self, "mcp_manager", None) is not None:
            sent.mcp_register(self.mcp_manager.servers())

    def mcp_add_server_guardian(self, cfg: dict) -> list:
        """【P-Muse·第4步】守门人模式下接入 MCP server 的发现流程:
        含 headers 的完整配置只在这一次调用里经手,推给守门人后主进程立即丢弃 headers,
        只存非敏感元数据(id/name/url/transport)。发现(list_tools)由守门人代执行。"""
        meta = {k: cfg[k] for k in ("id", "name", "url", "transport")}
        full = [dict(s) for s in self.mcp_manager.servers()]
        full = [s for s in full if s.get("id") != cfg["id"]]  # 去掉旧同 id,避免重复
        full.append(cfg)                                      # 新 server 带 headers
        sent = self.sentinel
        if sent is None:
            # 无守门人 → 不可能代执行/代发现;fail-closed 而不是 AttributeError。
            raise RuntimeError("守门人未启用(FOXCLAW_GUARDIAN=0),无法走守门人模式接入 MCP")
        sent.mcp_register(full)                               # 全量推给守门人(含这次 headers)
        tools = sent.mcp_list_tools(cfg["id"])                # 守门人代发现(失败抛异常)
        self.mcp_manager.restore(meta)                        # 主进程只存元数据
        self.mcp_store.save(self.mcp_manager.servers())       # 落盘也无 headers
        return tools

    @property
    def search_runtime(self):
        """认知搜索内核(HSR)。经能力中枢惰性解析(首次触发 _build_search_runtime)。"""
        return self.capabilities.get("search_runtime")

    def _build_search_runtime(self):
        """构造 HSR:内部三源全连通(HMR记忆 + HKC知识 knowledge_port + Agent态势 agent_port)。
        由 CapabilityRegistry 首次解析 "search_runtime" 时调用并缓存。
        Agent 侧经 self._caps.get("search_runtime") 反向取用,故此处**不再注入** Agent。"""
        from .search import SearchRuntime, HKCReadPort, AgentSourcePort
        hkc = self.hkc   # 触发 HKC 惰性装配(用作只读知识端口)
        kp = HKCReadPort(client=hkc.client) if (hkc and hkc.available) else None
        ap = AgentSourcePort(
            agents=self._agents,
            finance_runtime=getattr(self, "finance_runtime", None),
            learning_runtime=getattr(self, "learning_runtime", None),
            itinerary_runtime=getattr(self, "itinerary_runtime", None),
            thought_chain=self.thought_chain,
            skill_registry=getattr(self, "skill_registry", None),
            hmr=self.hmr,
        )
        return SearchRuntime(hmr=self.hmr, knowledge_port=kp, agent_port=ap)

    def get_agent(self, agent_id: str):
        return self._agents.get(agent_id)

    def get_all_status(self) -> dict:
        return {
            agent_id: agent.get_status()
            for agent_id, agent in self._agents.items()
        }

    async def run(self, task: str, agent_id: str = "supervisor",
                  context: dict | None = None) -> dict:
        """
        快捷运行接口
        默认由主管 Agent 处理，它会根据任务类型决定路由

        context 现在透传给 Agent(此前 API 层收了 context 但这里丢掉,导致调用方
        无法传 `_resume_task_id` 之类控制项):`{"_resume_task_id": ...}` 即断点续跑。
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return {"error": f"未知 Agent: {agent_id}"}
        return await agent.run_task(task, context=context)

    async def aclose(self):
        """系统级异步 teardown:释放持有原生资源的子系统(浏览器专用线程/会话)。
        由 API lifespan 关停时调用;幂等。"""
        svc = getattr(self, "browser_service", None)
        if svc is not None:
            try:
                await svc.close()
            except Exception:
                pass
        # 真值结晶器:关停后台线程池(即使从未启用也安全,幂等)
        tc = getattr(self, "truth_crystallizer", None)
        if tc is not None:
            try:
                tc.close()
            except Exception:
                pass
        # HKC:仅当被惰性装配过才需释放(未访问过则从未开句柄)。
        # 必须清两层引用:
        # 1) self._hkc_service (Foxclaw 属性) — 否则 HKCService 实例及 _container 闭包持有的
        #    SQLite 连接在 CPython 引用计数下不会立即 close,残留 events.db/hkc.db 文件锁
        # 2) hkc_api.container._container (vendor 全局单例) — foxclaw.knowledge.init_hkc
        #    第 143 行把它注册进去,reset_container() 才会同时关 SQLite + 清引用。
        # 不清会导致 lifespan teardown 后的 rmtree 报 PermissionError [WinError 32]。
        hkc_svc = getattr(self, "_hkc_service", None)
        hkc_was_inited = getattr(self, "_hkc_inited", False) or hkc_svc is not None
        if hkc_svc is not None:
            try:
                hkc_svc.close()
            except Exception:
                pass
            self._hkc_service = None
        # vendor 全局单例 — 即使 lazy hkc 没被访问,启动期 Foxclaw.__init__ 早期预装配
        # (line 67-85) 已经把 container 塞进 _cm._container;aclose 必须清。
        if hkc_was_inited:
            try:
                from hkc_api.container import reset_container
                reset_container()
            except Exception:
                pass
        self._hkc_inited = False


