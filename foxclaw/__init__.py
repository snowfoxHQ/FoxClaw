"""
Foxclaw — 系统入口
统一初始化：HMR + ThoughtChain + Harness + 三个 Agent
"""

import os
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import anthropic

for _stream in (sys.stdout, sys.stderr):
    try:
        _reconf = getattr(_stream, "reconfigure", None)
        if _reconf is not None:
            _reconf(encoding="utf-8", errors="replace")
    except Exception:
        pass

__version__ = "0.1.4"

from .core.thought_chain import ThoughtChain


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

        api_key = anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.client: "anthropic.Anthropic | None" = None
        if api_key:
            try:
                import anthropic
                self.client = anthropic.Anthropic(api_key=api_key)
            except Exception:
                self.client = None
        else:
            self.client = None

        try:
            from .knowledge import init_hkc as _init_hkc_early
            _early_hkc = _init_hkc_early(self.data_dir, api_key)
        except Exception as _e:
            _early_hkc = None
            print(f"[Foxclaw] ⚠ 启动期 HKC 预装配失败(继续,降级): {_e}")

        from .core.memory_adapter import create_memory_backend
        self.hmr = create_memory_backend(
            storage_path=str(self.data_dir / "hmr"),
            llm_api_key=api_key,
        )

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

        self._hkc_service = None
        self._hkc_inited = False

        self.thought_chain = ThoughtChain(
            db_path=str(self.data_dir / "thoughtchain.db")
        )

        from .core.sessions import SessionStore
        self.sessions = SessionStore(self.data_dir / "sessions")

        from .core.model_router import ModelRouter
        self.router = ModelRouter()

        from .core.token_meter import TokenMeter
        self.token_meter = TokenMeter(
            log_path=str(self.data_dir / "token_usage.jsonl"))
        self.router.meter = self.token_meter

        from .core.content_guard import ContentGuard
        self.content_guard = ContentGuard()
        self.router.content_guard = self.content_guard

        from .kernel import EventSpine, CapabilityRegistry, Scheduler
        self.event_spine = EventSpine()

        _maxc = os.environ.get("FOXCLAW_MAX_CONCURRENCY", "").strip()
        self.scheduler = Scheduler(
            max_concurrency=int(_maxc) if _maxc.isdigit() else None,
            spine=self.event_spine)

        from .core.governance_audit import attach as _attach_gov_audit
        self.governance_auditor = _attach_gov_audit(self.event_spine, self.data_dir)

        from .kernel import PolicyGate
        from .skills.permission import evaluate as _perm_eval

        action_evaluator = _perm_eval
        self.sentinel = None
        if os.environ.get("FOXCLAW_GUARDIAN", "0").strip() in ("1", "true", "True", "yes", "on"):
            from .kernel.sentinel_client import SentinelClient
            self.sentinel = SentinelClient(data_dir=str(self.data_dir))
            action_evaluator = self.sentinel.evaluate
        self.policy_gate = PolicyGate(spine=self.event_spine, action_evaluator=action_evaluator,
                                      auditor=self.governance_auditor)

        self.capabilities = CapabilityRegistry()
        self.capabilities.register("event_spine", self.event_spine)
        self.capabilities.register("scheduler", self.scheduler)
        self.capabilities.register("policy_gate", self.policy_gate)
        self.capabilities.register("memory", self.hmr)
        self.capabilities.register("thought_chain", self.thought_chain)
        self.capabilities.register("router", self.router)
        if self.sentinel is not None:
            self.capabilities.register("sentinel", self.sentinel)

        from .core.shared_write import SharedWritePolicy, attach as _attach_shared_write
        self.shared_write_policy = SharedWritePolicy()
        _attach_shared_write(self.hmr, self.shared_write_policy,
                             spine=self.event_spine)

        from .core.audit_sink import attach as _attach_shared_write_audit
        self.shared_write_audit = _attach_shared_write_audit(
            self.event_spine, self.data_dir)

        from .core.sensitive_write import (
            PENDING_FILE,
            SensitiveWritePolicy,
            attach as _attach_sensitive_write,
        )
        self.sensitive_write_policy = SensitiveWritePolicy(
            pending_path=Path(self.data_dir) / PENDING_FILE)
        _attach_sensitive_write(self.hmr, self.sensitive_write_policy,
                                spine=self.event_spine)

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

        self._agents = {
            "supervisor": self.supervisor,
            "finance":    self.finance,
            "office":     self.office,
        }
        self.capabilities.register("agents", self._agents)

        self.supervisor.register_subagents({
            "finance":   self.finance,
            "office":    self.office,
        })

        from .finance import FinanceRuntime
        self.finance_runtime = FinanceRuntime(
            policy_path=str(self.data_dir / "finance_policy.yaml"),
            hmr=self.hmr,
        )
        self.finance.attach_runtime(self.finance_runtime)

        from .learning import LearningRuntime
        self.learning_runtime = LearningRuntime(
            hmr=self.hmr, hkc_provider=lambda: self.hkc,
            persist_path=str(self.data_dir / "learning.json"))
        self.office.attach_runtime(self.learning_runtime)

        from .itinerary import ItineraryRuntime
        self.itinerary_runtime = ItineraryRuntime(
            amap_key=os.environ.get("AMAP_MAPS_API_KEY"), hmr=self.hmr)
        self.office.attach_itinerary(self.itinerary_runtime)

        from .orchestration import Orchestrator
        self.orchestrator = Orchestrator(self.capabilities, auditor=self.governance_auditor)
        self.supervisor.attach_orchestrator(self.orchestrator)
        self.capabilities.register("orchestrator", self.orchestrator)

        from .skills import build_default_registry, SkillAdapter
        self.skill_registry = build_default_registry()
        from .skills.store import SkillStore
        self.skill_store = SkillStore(self.data_dir)
        self.skill_store.load(self.skill_registry)
        from .core.confirm_nonce import ConfirmationBroker
        self.confirm_broker = ConfirmationBroker()
        self.skill_adapter = SkillAdapter(
            self.skill_registry, mcp_clients={}, policy_gate=self.policy_gate,
            sentinel=self.sentinel, confirms=self.confirm_broker)
        from .skills.mcp_client import McpManager
        from .skills.mcp_store import McpStore
        self.mcp_manager = McpManager()
        self.mcp_store = McpStore(self.data_dir)
        for _cfg in self.mcp_store.load():
            self.mcp_manager.restore(_cfg)
        self.skill_adapter.mcp_clients = self.mcp_manager
        self.sync_mcp_servers_to_sentinel()
        from .skills.policy import SkillPolicy
        self.skill_policy = SkillPolicy(self.data_dir)
        self.skill_adapter.skill_policy = self.skill_policy
        from .skills.markdown_skill import MarkdownSkillRegistry
        from .skills.markdown_store import MarkdownStore
        self.md_skills = MarkdownSkillRegistry()
        self.md_skills.policy = self.skill_policy
        self.md_store = MarkdownStore(self.data_dir)
        self.md_store.load(self.md_skills)
        from .skills.markdown_skill import load_builtin_dir
        load_builtin_dir(self.md_skills, Path(__file__).resolve().parent.parent / "skills")
        for _ag in self._agents.values():
            _ag.md_skills = self.md_skills
            _ag.hooks = self.skill_adapter.hooks
            _ag.attach_skills(self.skill_registry, self.skill_adapter)
        self.supervisor.skill_registry = self.skill_registry
        from .skills.pipeline import AHSPipeline
        self.ahs_pipeline = AHSPipeline(self.skill_registry, self.skill_adapter)
        from .core.system_bus import SystemBus
        from .core.truth_pipeline import TruthPipeline
        self.system_bus = SystemBus(spine=self.event_spine)
        self.truth_pipeline = TruthPipeline(self.system_bus, hmr=self.hmr)
        from .knowledge import TruthCrystallizer
        self.truth_crystallizer = TruthCrystallizer(
            hkc_provider=lambda: self.hkc,
            enabled=os.environ.get("FOXCLAW_TRUTH_TO_HKC", "") == "1")
        self.system_bus.subscribe("truth.appended", self.truth_crystallizer.on_truth,
                                  exact=True)
        from .kernel import ProcessManager
        self.process_manager = ProcessManager(bus=self.system_bus)
        self.scheduler.process_manager = self.process_manager
        from .cognitive import CognitiveStateTracker
        self.cognitive_state = CognitiveStateTracker(
            self.system_bus, process_manager=self.process_manager)
        if "supervisor" in self._agents:
            self._agents["supervisor"]._system_bus = self.system_bus
            self._agents["supervisor"]._process_manager = self.process_manager

        from .core.recovery import RestartRecovery
        self.recovery = RestartRecovery(
            log_path=str(self.data_dir / "task_log.jsonl"),
            spine=self.event_spine)
        for _a in self._agents.values():
            _a._recovery = self.recovery
        self.interrupted_tasks = self.recovery.scan_interrupted()
        self.recovery_plan = self.recovery.plan_recovery(self.interrupted_tasks)

        from .browser import BrowserService
        self.browser_service = BrowserService(
            headless=os.environ.get("FOXCLAW_BROWSER_HEADLESS", "1") != "0")

        self._hkc_api_key = api_key
        self._hkc_service = None
        self._hkc_inited = False
        self._hkc_lock = threading.Lock()

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
        full = [s for s in full if s.get("id") != cfg["id"]]
        full.append(cfg)
        sent = self.sentinel
        if sent is None:
            raise RuntimeError("守门人未启用(FOXCLAW_GUARDIAN=0),无法走守门人模式接入 MCP")
        sent.mcp_register(full)
        tools = sent.mcp_list_tools(cfg["id"])
        self.mcp_manager.restore(meta)
        self.mcp_store.save(self.mcp_manager.servers())
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
        hkc = self.hkc
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

    def list_sensitive_pending(self) -> list:
        """待用户同意的高敏感记忆写入。未挂策略时返回空列表。"""
        policy = getattr(self, "sensitive_write_policy", None)
        return policy.pending.list() if policy is not None else []

    def approve_sensitive_write(self, pending_id: str) -> bool:
        """用户同意后落库。这是全仓唯一允许传 consent=True 的地方。"""
        policy = getattr(self, "sensitive_write_policy", None)
        if policy is None:
            return False
        entry = policy.pending.pop(pending_id)
        if entry is None:
            return False
        self.hmr.ingest(
            entry.get("content", ""),
            type=entry.get("type", "concept"),
            agent_id=entry.get("agent_id") or None,
            consent=True,
        )
        return True

    def reject_sensitive_write(self, pending_id: str) -> bool:
        """用户拒绝:丢弃待确认项,不落库。"""
        policy = getattr(self, "sensitive_write_policy", None)
        return bool(policy is not None and policy.pending.pop(pending_id) is not None)

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
        tc = getattr(self, "truth_crystallizer", None)
        if tc is not None:
            try:
                tc.close()
            except Exception:
                pass
        hkc_svc = getattr(self, "_hkc_service", None)
        hkc_was_inited = getattr(self, "_hkc_inited", False) or hkc_svc is not None
        if hkc_svc is not None:
            try:
                hkc_svc.close()
            except Exception:
                pass
            self._hkc_service = None
        if hkc_was_inited:
            try:
                from hkc_api.container import reset_container
                reset_container()
            except Exception:
                pass
        self._hkc_inited = False


