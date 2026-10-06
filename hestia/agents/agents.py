"""
五个具体 Agent 实现

Agent 结构：
  - SupervisorAgent  （主管）：总协调，任务分发，感知全局状态
  - AssistantAgent   （助手）：主管助理，执行协同，信息整理
  - FinanceAgent     （财务）：理财规划，收支追踪，财务报告
  - AdminAgent       （行政）：行程管理，日程规划，会议安排
  - OfficeAgent      （办公）：学习助理，知识储备，文档处理

每个 Agent 有独立的：
  - 系统提示词（个性化认知边界）
  - 允许工具类别（Harness 约束）
  - 记忆存储（HMR Workspace 隔离）
"""

import asyncio
import json
import time
from datetime import datetime
from .base import CognitiveAgent
from ..planning import planner as P   # 统一规划原语(P1-2:关键词命中单一真源)

# ── 多步链式上下文 ────────────────────────────────────────────
# 注入的前序步数与单步字符必须封顶:长链会把提示词越滚越大(且越往后越贵/越容易超窗)。
PRIOR_MAX_STEPS = 3      # 只带最近 N 步(更早的通常已被后续步骤消化)
PRIOR_MAX_CHARS = 300    # 单步输出截断


def _with_prior(subtask: str, prior: list) -> str:
    """把前序步骤结果并进下一步的任务文本,让多步真正成链。

    为什么并进 task 文本而不是只塞 context:各领域 Agent 的 `_execute` 是各自实现的,
    未必会去读 context;而 task 文本必定进提示词 —— 这样对**所有** Agent 都生效,
    且无需改动任何一个 Agent。context 里也照样放一份结构化的(两手准备)。
    """
    if not prior:
        return subtask
    lines = "\n".join(
        f"第{p['step']}步({p['agent']}):{str(p.get('output',''))[:PRIOR_MAX_CHARS]}"
        for p in prior[-PRIOR_MAX_STEPS:]
    )
    return f"{subtask}\n\n[前序步骤结果 —— 若与本步相关,请据此作答]\n{lines}"


def _fmt_mem(m: dict, max_chars: int | None = None) -> str:
    """把一条召回记忆格式化为带结构信息的文本(标题/相关度/共享标记)。

    替代此前各领域 Agent 只取 content 的注入——那会丢弃 title/score/shared 等
    结构信息,让 LLM 无法区分"高相关共享决策"与"低相关私有闲聊"。字段可能缺失
    (不同召回路径返回的 dict 字段不一),全部安全降级:title 空 / score 非数字 /
    无 shared 时退化为纯 content,行为与改前一致。max_chars=None 不截断。
    """
    title = (m.get("title") or "").strip()
    content = (m.get("content") or "").strip()
    if max_chars is not None:
        content = content[:max_chars]
    parts = [title] if title else []
    score = m.get("score")
    if score is not None:
        try:
            parts.append(f"相关度{float(score):.2f}")
        except (TypeError, ValueError):
            pass
    if m.get("shared"):
        parts.append("[共享]")
    head = " ".join(parts)
    return f"{head}：{content}" if head else content


# ── P0-1 反思重规划:执行结果判定 ────────────────────────────
def _dispatch_failed(dr) -> bool:
    """判断多步 dispatch 结果是否含失败/未完成步骤(触发反思重规划的前提)。"""
    if not isinstance(dr, dict):
        return False
    result = dr.get("result") or {}
    if not isinstance(result, dict):
        return False
    if result.get("dispatched_to") not in ("multi", "parallel"):
        return False
    if result.get("blocked"):
        return True
    steps = result.get("parallel_steps") or result.get("multi_steps") or []
    return any((s.get("failed") or s.get("blocked"))
               for s in steps if isinstance(s, dict))


def _extract_step_results(dr) -> list:
    """从 dispatch_result 提取各步结果(供 LLM 反思:含成功/失败状态与输出)。"""
    if not isinstance(dr, dict):
        return []
    result = dr.get("result") or {}
    if not isinstance(result, dict):
        return []
    steps = result.get("parallel_steps") or result.get("multi_steps") or []
    out = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        out.append({
            "subtask": s.get("subtask") or s.get("step"),
            "agent": s.get("agent"),
            "output": s.get("output", ""),
            "blocked": bool(s.get("blocked")),
            "failed": bool(s.get("failed")),
        })
    return out


# ══════════════════════════════════════════════════════════
# Agent 1: SupervisorAgent — 主管
# ══════════════════════════════════════════════════════════

class SupervisorAgent(CognitiveAgent):
    AGENT_ID = "supervisor"
    AGENT_NAME = "主管 Agent"
    ALLOWED_TOOL_CATEGORIES = {"read", "write", "calendar", "finance", "network"}
    IS_ROUTER = True  # 调度层:豁免语义漂移拦截(接收任意输入是其本职)

    @property
    def _role_prompt(self) -> str:
        return """你是 Hestia OS 的主管 Agent，负责总体协调和任务分发。

你的核心职责：
1. 理解用户的整体目标，将复杂任务分解为子任务
2. 根据任务性质，决定分配给哪个子 Agent（财务/行政/办公）
3. 整合各 Agent 的返回结果，向用户呈现统一的答案
4. 维护全局状态，感知各 Agent 的工作进展

你的工作风格：
- 清晰、高效、以结果为导向
- 对用户的长期目标保持记忆，而不是只处理当前请求
- 当任务超出某个子 Agent 能力范围时，主动说明

注意：你有访问所有类别工具的权限，但应该谨慎使用，优先委派给专门的子 Agent。

关于联网：你具备实时联网搜索能力——需要最新/实时信息（版本、新闻、价格等）时，结果会自动注入上下文，或由你调用工具获取；直接基于检索结果作答，没查到就如实说"暂未查到"，别声称"没有联网能力"。（不编造实时信息的通则见内核·真相优先，不再赘述。）"""

    def register_subagents(self, subagents: dict):
        """注入可调度的子 Agent 登记表。"""
        self._subagents = subagents
        # 【深审硬化·消除懒建 race】主管单例会被并发任务共享,_scheduler/_steering_queue 原先在
        # _schedule/_dispatch_parallel 里 `if not hasattr` 懒建——并发下是 check-then-act race
        # (现因 steering 队列 batch 隔离 + scheduler 无状态而良性,但依赖此实现细节脆弱)。改在
        # 本方法(HestiaOS 启动时单线程调一次)**预建**,并发任务到达时属性已就绪、不再 race。
        # 保留下游懒建作 standalone(未 register)兜底。
        from ..core.scheduler import CognitiveScheduler
        from ..core.steering_queue import SteeringQueue
        if not hasattr(self, "_scheduler"):
            self._scheduler = CognitiveScheduler()
        if not hasattr(self, "_steering_queue"):
            self._steering_queue = SteeringQueue(lease_seconds=120)

    def attach_orchestrator(self, orchestrator):
        """挂载编排引擎:全局态势 + 跨 Agent 请求路由。"""
        self.orch = orchestrator

    def global_situation(self) -> dict:
        """主管的驾驶舱视图:汇总三个内核状态。"""
        orch = getattr(self, "orch", None)
        if orch is None:
            return {"error": "编排引擎未挂载"}
        sit = orch.gather_situation()
        self._think(
            content=f"全局态势汇总:{len(sit.alerts)} 条跨域告警 "
                    f"(财务/学习/行程)",
            node_type="reflection", conf=0.9,
        )
        return sit.to_dict()

    def cross_request(self, from_agent: str, to_agent: str,
                      request_type: str, payload: dict, reason: str = "") -> dict:
        """发起跨 Agent 请求,目标 Agent 用自己的守门人裁决。"""
        orch = getattr(self, "orch", None)
        if orch is None:
            return {"error": "编排引擎未挂载"}
        from hestia.orchestration import CrossAgentRequest
        req = CrossAgentRequest(from_agent=from_agent, to_agent=to_agent,
                                request_type=request_type, payload=payload,
                                reason=reason)
        # 走协商闭环:被拒时给发起方让步重报的机会。对未实现 concede 的 Agent 而言
        # 只会跑一轮 → 与原先的一次性 route_request 逐字等价(零行为变更)。
        result = orch.negotiate(req)
        rounds = len(result.negotiation) or 1
        self._think(
            content=f"跨 Agent 编排:{from_agent}→{to_agent} "
                    f"({request_type}) 裁决={result.status} 协商={rounds}轮",
            node_type="decision", conf=0.88,
        )
        return result.to_dict()

    # 路由关键词表:无 LLM 时也能基于关键词分发
    ROUTING_KEYWORDS = {
        "finance": ["收支", "支出", "预算", "理财", "投资", "储蓄", "财务", "消费", "报销", "账"],
        "admin":   ["日程", "行程", "会议", "安排", "提醒", "出差", "预约", "日历", "计划表"],
        "office":  ["学习", "论文", "知识", "总结", "研究", "文档", "写作", "资料", "搜索", "查"],
        "assistant": ["整理", "汇总", "邮件", "协助", "沟通", "联系"],
    }

    # 子 Agent 职责描述(P0-1 LLM 规划用):喂给 submit_plan 工具做 agent 枚举约束
    SUBAGENT_DESC = {
        "finance": "财务 Agent：理财、收支、预算、风控、对账",
        "admin": "行政 Agent：日程、行程、出差、地图规划",
        "office": "办公 Agent：学习、知识管理、研究、写作",
        "assistant": "助手 Agent：通用任务、整理、沟通协助",
    }

    # 元问题关键词:这类"系统级/自我介绍"问题由主管自己回答,不分派
    META_KEYWORDS = ["你会做什么", "你能做什么", "你是谁", "你能干", "你会干",
                     "介绍一下", "介绍下", "自我介绍", "有什么功能", "能帮我做什么",
                     "你都能", "怎么用", "如何使用", "你好", "在吗", "干什么",
                     "你能联网", "你可以联网", "能联网吗", "支持联网", "会联网"]

    def _is_meta(self, task: str) -> bool:
        """判断是否为系统级/介绍类元问题(主管自答,不分派)。"""
        t = task.strip()
        has_meta = any(k in t for k in self.META_KEYWORDS)
        has_biz = any(P.keyword_hits(t, kws) for kws in self.ROUTING_KEYWORDS.values())
        return has_meta and not has_biz

    def _route(self, task: str, sched=None) -> str:
        """决定任务派给哪个子 Agent。

        综合决策:关键词命中(主)+ 调度意图(辅)。
        - 关键词明确命中 → 直接采用(领域词最可靠)
        - 关键词不明 + 调度意图为 memory/reasoning → 倾向 office(知识/研究)
        - 都不明 → 助手兜底
        """
        # 关键词命中计数走统一原语(negation=False,与原计数逐字一致)
        scores = {aid: P.keyword_hits(task, kws)
                  for aid, kws in self.ROUTING_KEYWORDS.items()}
        best = max(scores, key=scores.get)
        if scores[best] > 0:
            return best
        # 关键词不明:用调度意图做辅助判断
        if sched is not None:
            intent = getattr(getattr(sched, "intent", None), "category", None)
            if intent in ("reasoning", "memory"):
                return "office"     # 推理/记忆类 → 知识研究 Agent
            if intent == "workflow":
                return "assistant"  # 工作流类 → 助手统筹
        return "assistant"          # 无明确归属 → 助手兜底

    def _has_keyword_hit(self, task: str) -> bool:
        """任务是否命中任一领域关键词(命中 = 关键词路由可信,不必再问 LLM)。"""
        return any(P.keyword_hits(task, kws) for kws in self.ROUTING_KEYWORDS.values())

    # 【已删除 · 请勿凭记忆加回】这里原有一个 `_llm_classify(task) -> Optional[str]`:
    # 关键词未命中时用一次 LLM 把任务分诊到领域 Agent。它已被「无关键词命中 → 主管直接
    # 自答」分支取代并成为死代码(全仓库零调用点),故删除。当时的取舍:每次分诊要多一次
    # 约 5s 的串行 LLM 往返,正面对话不值当,于是改为主管自答。P1-1 后该分支在
    # pipeline/supervisor_stages.py 的 route(决策)/dispatch(执行)两个 stage 里。
    # 如需恢复精修,应从 git 历史取回本方法,并同时改回那两个 stage、补回对应测试
    # —— 只加回方法不接线等于再制造一处死代码。

    def _plan_task(self, task: str, sched=None):
        """用 planner 判断任务是否需要拆解(多步)。

        返回 (plan, step_routes) 或 (None, None)。
        step_routes: [(子任务文本, 该去的子Agent)],让 planner 的拆解真正映射到 Agent。

        【P0-1】HESTIA_LLM_PLAN=1 时优先 LLM 规划(function-calling 拆解+路由);
        不可用/失败/单步 → 回退下方规则拆解(行为不变)。
        """
        llm = self._llm_plan(task)
        if llm is not None:
            return llm
        try:
            from ..skills.pipeline import plan as ahs_plan
            reg = getattr(self, "skill_registry", None)
            if reg is None:
                return None, None
            p = ahs_plan(task, reg)
            if len(p.tasks) <= 1:
                return None, None
            # 为每个子任务单独算路由(关键词为主)→ planner 拆解真正落到不同 Agent
            step_routes = [(t.name, self._route(t.name)) for t in p.tasks]
            return p, step_routes
        except Exception:
            return None, None

    def _llm_plan(self, task: str):
        """【P0-1】LLM 驱动规划：function-calling 拆解任务并路由到子 Agent。

        返回 (plan, step_routes) 或 None(未开启/不支持 tools/失败/单步)。
        单步返回 None 交回 _route 关键词路由，不改变单步行为。
        """
        from ..planning.llm_planner import llm_plan, llm_plan_enabled
        if not llm_plan_enabled():
            return None
        if self.router.is_stub or not self.router.supports_tools():
            return None
        subagents = getattr(self, "_subagents", {}) or {}
        if not subagents:
            return None
        catalog = {aid: self.SUBAGENT_DESC.get(aid, getattr(a, "AGENT_NAME", aid))
                   for aid, a in subagents.items()}
        try:
            result = llm_plan(self.router, task, catalog)
        except Exception:
            return None
        if not result or not result.get("steps"):
            return None
        steps = result["steps"]
        if len(steps) <= 1:
            return None            # 单步 → 交回 _route 关键词路由，不改变单步行为
        from ..skills.pipeline import Plan, PlanTask
        n = len(steps)
        parallel = result.get("flow") == "parallel"
        tasks = [
            PlanTask(id=f"T{i+1}", name=s["name"], suggested_skill=None,
                     dependencies=[] if parallel else ([f"T{i}"] if i > 0 else []))
            for i, s in enumerate(steps)
        ]
        plan = Plan(intent=result.get("intent") or task, tasks=tasks,
                    flow_type=result.get("flow") or "sequential",
                    steps=[f"T{i+1}" for i in range(n)])
        step_routes = [(s["name"], s["agent"]) for s in steps]
        return plan, step_routes

    def _replan_enabled(self) -> bool:
        """【P0-1】反思重规划是否可用：opt-in + 模型支持 tools + 有子 Agent。"""
        from ..planning.llm_planner import replan_enabled
        return (replan_enabled()
                and not self.router.is_stub
                and self.router.supports_tools()
                and bool(getattr(self, "_subagents", {})))

    async def _replan_and_redo(self, task, sched, session_id, subagents,
                               context, memories, dispatch_result, max_rounds: int = 2):
        """【P0-1】反思重规划：多步执行含失败时，用 LLM 反思并产出修正计划，重新执行。

        返回最终 dispatch_result（成功则为最后一次执行结果；失败/不可用则保留最后结果）。
        循环最多 max_rounds 轮防死循环；每轮先反思再执行，成功即停。
        """
        from ..planning.llm_planner import llm_replan
        catalog = {aid: self.SUBAGENT_DESC.get(aid, getattr(a, "AGENT_NAME", aid))
                   for aid, a in subagents.items()}
        current = dispatch_result
        for rnd in range(1, max_rounds + 1):
            prior = _extract_step_results(current)
            self._think(
                content=f"反思重规划 第{rnd}轮:检测到失败步骤,LLM 反思并产出修正计划",
                node_type="reflection", conf=0.85,
            )
            try:
                replan = await asyncio.to_thread(
                    llm_replan, self.router, task, prior, catalog)
            except Exception:
                replan = None
            if not replan or not replan.get("steps"):
                self._think(
                    content="反思判定:无法完成/无需重试,终止重规划",
                    node_type="reflection", conf=0.8,
                )
                break
            new_routes = [(s["name"], s["agent"]) for s in replan["steps"]]
            self._think(
                content=f"修正计划({len(new_routes)}步):"
                       + " → ".join(n for n, _ in new_routes),
                node_type="decision", conf=0.8,
            )
            if replan.get("flow") == "parallel":
                current = await self._dispatch_parallel(
                    task, new_routes, sched, session_id, subagents, context, memories)
            else:
                current = await self._dispatch_multi(
                    task, new_routes, sched, session_id, subagents, context, memories)
            if not _dispatch_failed(current):
                self._think(
                    content=f"重规划第{rnd}轮执行成功",
                    node_type="reflection", conf=0.9,
                )
                break
        return current

    def _schedule(self, task: str, subagents: dict):
        """认知调度:用 OS 式调度器算优先级/负载/抢占。返回 ScheduleDecision。"""
        from ..core.scheduler import CognitiveScheduler, SystemContext
        if not hasattr(self, "_scheduler"):
            self._scheduler = CognitiveScheduler()
        # 系统上下文:活跃 Agent 数 = 当前持有活跃 session 的子 Agent 数
        # (用真实可得的 _session_id 判断忙碌,而非不存在的 is_busy 属性)
        active = sum(1 for a in subagents.values()
                     if getattr(a, "_session_id", None))
        ctx = SystemContext(active_agents=active, queue_depth=0, system_load=0.1)
        return self._scheduler.schedule(task, ctx)

    async def _dispatch_multi(self, task, step_routes, sched, session_id,
                              subagents, context, memories) -> dict:
        """多 Agent **链式**顺序执行:每个子任务派给各自的 Agent,后一步能看到前序步骤的结果。

        (原先每步都只拿到同一份原始 context,等于 N 个互不相干的任务排队跑——
         「查本月餐饮花了多少,再据此定预算」的第 2 步根本拿不到第 1 步的数字。)"""
        pm = getattr(self, "_process_manager", None)
        bus = getattr(self, "_system_bus", None)
        steps_out = []
        prior = []                 # 已完成步骤的结果,供后续步骤参考
        any_blocked = False
        for idx, (subtask, ag) in enumerate(step_routes, 1):
            if ag not in subagents:
                ag = "assistant"
            proc = pm.create(subtask, agent_id=ag, schedule=sched.to_dict()) if pm else None
            self._think(
                content=f"多步执行 第{idx}步:「{subtask}」→ {ag}",
                node_type="tool_intent", tool_name=f"dispatch_{ag}", conf=0.9,
            )
            if proc:
                pm.mark_running(proc.process_id)
            # _no_emit:子 Agent 不自发 execution.completed,由下方主管统一发(带 schedule),避免双发
            # prior_steps 同时也放进 context,便于管线/Agent 按结构化方式取用
            # 补超时(D 类修复):与 _dispatch_single / _dispatch_parallel 三处标准一致,
            # 子 Agent 卡死不再让整条多步链永久挂起。
            _timeout = float(context.get("parallel_timeout", 120))
            try:
                sub_result = await asyncio.wait_for(
                    subagents[ag].run_task(
                        _with_prior(subtask, prior),
                        {**context, "_no_emit": True, "prior_steps": list(prior)}),
                    timeout=_timeout)
            except asyncio.TimeoutError:
                _note = f"第{idx}步子 Agent {ag} 执行超时({_timeout:g}s),该步未完成"
                if proc:
                    pm.mark_failed(proc.process_id, reason="subagent_timeout")
                sub_result = {"result": _note, "error": "subagent_timeout", "agent": ag}
            sub_output = sub_result.get("result", "")
            if isinstance(sub_output, dict):
                sub_output = json.dumps(sub_output, ensure_ascii=False)
            # 未完成(含超时)一律视为 blocked —— 不得让超时步骤被当成
            # 成功结果进入 prior 链,否则后续步骤会基于"不存在的产出"继续推理。
            blocked = sub_result.get("error") in ("harness_blocked", "subagent_timeout")
            any_blocked = any_blocked or blocked
            if proc:
                (pm.mark_failed(proc.process_id, reason=sub_result.get("error") or "")
                 if blocked else pm.mark_completed(proc.process_id))
            # 展示用的 subtask 保留**原始**文本(不含前序上下文前言,免污染 UI)
            steps_out.append({"step": idx, "subtask": subtask, "agent": ag,
                              "output": str(sub_output)[:200], "blocked": blocked})
            if not blocked and sub_output:      # 被拦截/空输出的步骤无可参考内容,不入链
                # 入链即截断:渲染时本就只用最近 PRIOR_MAX_STEPS 步 × PRIOR_MAX_CHARS 字符,
                # 存全量纯属浪费——且 prior 会被 list(prior) 复制进**每个**后续步骤的 context,
                # 长链 + 大输出会滚成可观的内存与拷贝开销。
                prior.append({"step": idx, "agent": ag,
                              "output": str(sub_output)[:PRIOR_MAX_CHARS]})
            # 【P1-1】execution.completed 不再逐子任务手动 publish——改由 base 模板
            # `_emit_execution` 统一发一条**任务级**事件(子 Agent 均带 _no_emit=True)。
            # 无总线(部分测试/独立调用)→ 主管手动兜底沉淀记忆,与单步路径一致
            if bus is None and not blocked:
                self._store_memory(
                    content=f"任务「{subtask}」由 {ag} 处理。要点:{str(sub_output)[:200]}",
                    mem_type="decision",
                    title=f"多步记录_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{idx}",
                    tags=["dispatch", "multi_step", f"to:{ag}"],
                    shared=True,
                )

        summary = f"多步任务完成,共 {len(steps_out)} 步,跨 {len({s['agent'] for s in steps_out})} 个 Agent"
        return {
            "result": {
                "dispatched_to": "multi",
                "multi_steps": steps_out,
                "blocked": any_blocked,
                "summary": summary,
            },
            "schedule": sched.to_dict(),
            "plan": {"multi_step": True, "multi_agent": True,
                     "steps": [s["subtask"] for s in steps_out]},
            "session_id": session_id,
            "agent": self.AGENT_ID,
            "thought_summary": self.thought_chain.get_summary(self.AGENT_ID),
        }

    async def _dispatch_parallel(self, task, step_routes, sched, session_id,
                                 subagents, context, memories) -> dict:
        """并行 fork-join:无依赖子任务并发派给不同 Agent,租约队列汇聚结果。

        并发安全设计(与全系统"线程安全铁律"一致):
          · 跨 Agent 并行,但**同一 Agent 的多个子任务在组内串行** —— 因 Agent 实例持有
            _session_id/_parent_node_id 这类会话态,同实例并发会串味;
          · 主管**自身**的 _think 只在 fork 前 / join 后串行调用,绝不在并发分支里碰,
            避免主管会话态竞态;
          · 子任务结果经 SteeringQueue(RLock)publish→lease→ack 回收:不丢(超时租约重入队)、
            不重(锁内挑选);ProcessManager/SystemBus/HMR 均已加锁,并发写审计不丢。
          · 每个子任务带超时(计算上的"租约"):某分支卡死不拖垮整批。
        """
        from ..core.steering_queue import SteeringQueue
        if not hasattr(self, "_steering_queue"):
            self._steering_queue = SteeringQueue(lease_seconds=120)
        q = self._steering_queue
        batch = f"batch_{session_id}"
        pm = getattr(self, "_process_manager", None)
        bus = getattr(self, "_system_bus", None)
        timeout = float(context.get("parallel_timeout", 120))

        agents_involved = sorted({ag for _, ag in step_routes})
        # fork 记录(串行,主管自身 think 不并发)
        self._think(
            content=f"并行 fork {len(step_routes)} 个子任务 → {agents_involved}",
            node_type="tool_intent", tool_name="fork_parallel", conf=0.9,
        )

        # 按 Agent 分组:不同组并行,同组内串行(护 per-agent 会话态)
        groups: dict[str, list] = {}
        for idx, (subtask, ag) in enumerate(step_routes):
            if ag not in subagents:
                ag = "assistant"
            groups.setdefault(ag, []).append((idx, subtask))

        async def run_group(ag, items):
            for idx, subtask in items:
                proc = pm.create(subtask, agent_id=ag,
                                 schedule=sched.to_dict()) if pm else None
                if proc:
                    pm.mark_running(proc.process_id)
                try:
                    sub = await asyncio.wait_for(
                        subagents[ag].run_task(subtask, {**context, "_no_emit": True}),
                        timeout=timeout)
                    out = sub.get("result", "")
                    if isinstance(out, dict):
                        out = json.dumps(out, ensure_ascii=False)
                    blocked = sub.get("error") == "harness_blocked"
                    if proc:
                        (pm.mark_failed(proc.process_id, reason="harness_blocked")
                         if blocked else pm.mark_completed(proc.process_id))
                    q.publish(batch, ag, {"idx": idx, "subtask": subtask, "agent": ag,
                                          "output": str(out)[:200], "blocked": blocked,
                                          "failed": False})
                    # 【P1-1】execution.completed 不再逐子任务手动 publish——
                    # 改由 base 模板 `_emit_execution` 统一发任务级事件。
                except asyncio.TimeoutError:
                    if proc:
                        pm.mark_failed(proc.process_id, reason="timeout")
                    q.publish(batch, ag, {"idx": idx, "subtask": subtask, "agent": ag,
                                          "output": f"(子任务超时 >{timeout}s)",
                                          "blocked": False, "failed": True})
                except Exception as e:
                    if proc:
                        pm.mark_failed(proc.process_id, reason=f"error:{e}")
                    q.publish(batch, ag, {"idx": idx, "subtask": subtask, "agent": ag,
                                          "output": f"(子任务失败: {e})",
                                          "blocked": False, "failed": True})

        t0 = time.perf_counter()
        await asyncio.gather(*[run_group(ag, items) for ag, items in groups.items()])
        wall_ms = round((time.perf_counter() - t0) * 1000, 1)

        # join:租约收集(不丢不重)→ 按 idx 还原原始顺序 → ack
        leased = q.lease(batch, max_items=len(step_routes) * 2)
        steps_out = []
        for it in leased:
            steps_out.append(it.payload)
            q.ack(it.item_id, it.lease_id)
        steps_out.sort(key=lambda s: s["idx"])

        any_blocked = any(s.get("blocked") for s in steps_out)
        any_failed = any(s.get("failed") for s in steps_out)
        # join 记录(串行)
        self._think(
            content=f"并行 join:{len(steps_out)}/{len(step_routes)} 步汇聚,"
                    f"墙钟 {wall_ms}ms,blocked={any_blocked},failed={any_failed}",
            node_type="reflection", conf=0.88,
        )
        # 无总线时兜底沉淀记忆(与顺序路径一致)
        if bus is None:
            for s in steps_out:
                if not s.get("blocked") and not s.get("failed"):
                    self._store_memory(
                        content=f"任务「{s['subtask']}」由 {s['agent']} 处理。"
                                f"要点:{str(s['output'])[:200]}",
                        mem_type="decision",
                        title=f"并行记录_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{s['idx']}",
                        tags=["dispatch", "parallel", f"to:{s['agent']}"], shared=True)

        return {
            "result": {
                "dispatched_to": "parallel",
                "parallel_steps": steps_out,
                "blocked": any_blocked,
                "summary": f"并行完成 {len(steps_out)} 步,跨 {len(groups)} 个 Agent,"
                           f"墙钟 {wall_ms}ms",
            },
            "schedule": sched.to_dict(),
            "plan": {"multi_step": True, "multi_agent": True, "parallel": True,
                     "wall_ms": wall_ms, "steps": [s["subtask"] for s in steps_out]},
            "session_id": session_id,
            "agent": self.AGENT_ID,
            "thought_summary": self.thought_chain.get_summary(self.AGENT_ID),
        }

    # ── 【P1-1】主管 pipeline 化:不再重写 run_task ────────────────────
    # 生命周期(会话/召回/返回体/Harness 兜底/hooks/流式/中断/统一 _emit_execution)
    # 全部走 base 的 _run_task_core;路由分发逻辑拆成 7 个 pipeline stage
    # (hestia/pipeline/supervisor_stages.py:recall→meta_check→schedule→route→plan→
    # dispatch→aggregate)。本类只保留:pipeline 覆盖、领域钩子、恢复记账、分发执行体。

    def _cognitive_pipeline(self):
        """主管的认知管线:SUPERVISOR_STAGES(路由分发),而非领域 Agent 的 DEFAULT_STAGES。"""
        pipe = getattr(self, "_pipeline_cache", None)
        if pipe is None:
            from ..pipeline import CognitivePipeline
            from ..pipeline.supervisor_stages import SUPERVISOR_STAGES
            pipe = self._pipeline_cache = CognitivePipeline(SUPERVISOR_STAGES)
        return pipe

    def _task_entities(self, task: str) -> list:
        """主管的目标基线实体(旧 run_task 用 _extract_entities 开会话,逐字保留)。"""
        return self._extract_entities(task)

    # 崩溃/重启恢复记账(与生命周期正交的横切关注点;_recovery 由 HestiaOS 装配注入,
    # standalone/测试无此属性 → 跳过)。经 base 生命周期钩子接入,run_task 与
    # run_task_stream 两条路径都会被记账。
    def _on_session_started(self, session_id: str, task: str, context: dict) -> None:
        _recovery = getattr(self, "_recovery", None)
        if _recovery is not None:
            _recovery.task_started(
                task_id=session_id, description=task,
                session_id=session_id, channel=(context or {}).get("channel", ""))

    def _on_task_settled(self, session_id: str, status: str) -> None:
        _recovery = getattr(self, "_recovery", None)
        if _recovery is not None:
            _recovery.task_finished(session_id, status=status)

    async def _dispatch_single(self, task, target, sched, session_id,
                               subagents, context, memories, plan_obj) -> dict:
        """单 Agent 分发(从原 _run_task_body 抽出,行为逐字一致,除两点 P1-1 变更):
        ① execution.completed 不再在此手动 publish——改由 base 模板 `_emit_execution`
          统一发一条**任务级**事件(子 Agent 调用带 _no_emit=True 仍不双发);
        ② 返回体不再带 session_id/agent/thought_summary(由 base 模板统一组装)。"""
        # 创建进程(任务即进程),携带调度决策 → 状态 scheduled
        pm = getattr(self, "_process_manager", None)
        proc = pm.create(task, agent_id=target, schedule=sched.to_dict()) if pm else None

        self._think(
            content=f"调度 {target} Agent 执行子任务",
            node_type="tool_intent",
            tool_name=f"dispatch_{target}",
            conf=0.92,
        )
        # 进程进入 running
        if proc:
            pm.mark_running(proc.process_id)
        # _no_emit:子 Agent 不自发,主管任务级统一 emit(见 base._emit_execution),避免双发
        #
        # 补超时(D 类修复:同文件内标准一致性)。此前只有并行路径
        # (_dispatch_parallel)有 asyncio.wait_for,单步/链式路径直接 await ——
        # 子 Agent 卡死(LLM 挂起/工具阻塞)会让主管任务**永久挂起**,且无日志
        # 无告警无释放。同一语义必须两套标准一致,超时值同样取 parallel_timeout。
        _timeout = float(context.get("parallel_timeout", 120))
        try:
            sub_result = await asyncio.wait_for(
                subagents[target].run_task(task, {**context, "_no_emit": True}),
                timeout=_timeout)
        except asyncio.TimeoutError:
            # 诚实交代:不伪造子 Agent 的产出,明确回报"超时未完成"
            _note = f"子 Agent {target} 执行超时({_timeout:g}s),已中止并如实回报未完成结果"
            if proc:
                pm.mark_failed(proc.process_id, reason="subagent_timeout")
            self._think(content=_note, node_type="decision", conf=0.95)
            sub_result = {"result": _note, "error": "subagent_timeout", "agent": target}

        # 汇聚子 Agent 结果
        sub_output = sub_result.get("result", "")
        if isinstance(sub_output, dict):
            sub_output = json.dumps(sub_output, ensure_ascii=False)
        blocked = sub_result.get("error") == "harness_blocked"

        # 进程终态:被拦截/出错→failed,否则→completed
        if proc:
            if blocked:
                pm.mark_failed(proc.process_id, reason="harness_blocked")
            else:
                pm.mark_completed(proc.process_id)

        self._think(
            content=f"{target} 返回结果(截断={blocked}):{str(sub_output)[:120]}",
            node_type="reflection",
            conf=0.88,
        )

        # 记忆沉淀:正常由真值管线(事件驱动,经 base._emit_execution)负责;
        # 无总线时(如部分测试场景)主管手动兜底,避免丢记忆。
        bus = getattr(self, "_system_bus", None)
        if bus is None:
            self._store_memory(
                content=f"任务「{task}」由 {target} 处理。要点:{str(sub_output)[:200]}",
                mem_type="decision",
                title=f"调度记录_{datetime.now().strftime('%Y%m%d_%H%M')}",
                tags=["dispatch", f"to:{target}"],
                shared=True,
            )

        return {
            "result": {
                "dispatched_to": target,
                "sub_result": sub_output,
                "blocked": blocked,
                "summary": f"主管已将任务派给 {subagents[target].AGENT_NAME} 处理并汇聚结果",
            },
            "schedule": sched.to_dict(),
            "plan": ({"steps": [t.name for t in plan_obj.tasks],
                      "multi_step": True} if plan_obj else {"multi_step": False}),
            "process_id": proc.process_id if proc else None,
        }

    async def _dispatch_fallback(self, task, context) -> dict:
        """子 Agent 不可用(未注册):主管自行处理。"""
        plan_response = await self._call_llm_web(task, [{
            "role": "user",
            "content": f"任务:{task}\n请直接给出处理方案。"
        }])
        self._think(content=f"主管自处理:{plan_response[:150]}", node_type="decision", conf=0.85)
        self._store_memory(
            content=f"任务:{task}\n方案:{plan_response[:300]}",
            mem_type="decision",
            title=f"主管处理_{datetime.now().strftime('%Y%m%d_%H%M')}",
            shared=True,
        )
        return {
            "result": {"dispatched_to": "supervisor", "sub_result": plan_response},
        }

    def _extract_entities(self, text: str) -> list[str]:
        """简单实体提取(规则版,后续可接 NER)。

        中英文都支持:英文取首字母大写的词;中文取出现的领域关键词
        (因中文无大小写,原首字母逻辑对中文无效)。
        """
        keywords = []
        # 英文:首字母大写的词
        for word in text.split():
            if len(word) > 3 and word[0].isupper():
                keywords.append(word)
        # 中文:命中路由关键词的领域词(作为实体线索)
        for kws in self.ROUTING_KEYWORDS.values():
            for kw in kws:
                if kw in text and kw not in keywords:
                    keywords.append(kw)
        return keywords[:10]


# ══════════════════════════════════════════════════════════
# Agent 2: AssistantAgent — 助手（主管助理）
# ══════════════════════════════════════════════════════════

class AssistantAgent(CognitiveAgent):
    AGENT_ID = "assistant"
    AGENT_NAME = "助手 Agent"
    ALLOWED_TOOL_CATEGORIES = {"read", "write", "network"}

    def attach_skills(self, registry, adapter):
        """挂载 skill 接入层:助手的万能工具箱。"""
        self.skill_registry = registry
        self.skill_adapter = adapter

    def list_skills(self) -> list:
        """列出助手当前可用的 skill。"""
        reg = getattr(self, "skill_registry", None)
        return reg.catalog() if reg else []

    def use_skill(self, task: str, args: dict = None, confirmed: bool = False) -> dict:
        """助手按任务匹配 skill 并调用,结果回填 ThoughtChain,过守门人。"""
        reg = getattr(self, "skill_registry", None)
        adp = getattr(self, "skill_adapter", None)
        if not (reg and adp):
            return {"error": "skill 接入层未挂载"}
        # 用升级版 router(三步打分 + 否定词检测),优于老的 reg.match 纯子串匹配
        try:
            from ..skills.pipeline import route as ahs_route
            r = ahs_route(task, reg)
            skill = reg.get(r.primary) if r.primary else None
        except Exception:
            skill = reg.match(task)   # 兜底
        if not skill:
            return {"matched": None, "message": "未匹配到合适的 skill"}
        self._think(
            content=f"匹配 skill:「{skill.name}」({skill.kind}/{skill.side_effect}) 处理:{task[:30]}",
            node_type="tool_intent", tool_name=skill.id, conf=0.9,
        )
        result = adp.invoke(skill, args or {}, confirmed=confirmed)
        self._think(
            content=f"skill 结果:{skill.name} → {result.status} · {result.message[:50]}",
            node_type="reflection", conf=0.88,
        )
        return {"matched": skill.to_dict(), "result": result.to_dict()}

    @property
    def _role_prompt(self) -> str:
        return """你是 Hestia OS 的助手 Agent，是主管的直接助理。

你的核心职责：
1. 执行主管分配的协调性任务
2. 整理和汇总来自其他 Agent 的信息
3. 处理跨 Agent 的信息流转
4. 维护用户的个人偏好和工作习惯记忆

你擅长：
- 信息整理与总结
- 跨领域的上下文维护
- 用户意图的精准理解
- 自然、友好的沟通方式

关于联网：你具备实时联网搜索能力——问最新/实时信息时，结果会自动注入上下文或由你调用工具获取；基于检索结果作答，没查到就如实说明，别声称"没有联网能力"。（不编造的通则见内核·真相优先。）"""

    # 生命周期由 base.run_task 模板统一;助手只实现领域执行(默认 entities/recall)。
    async def _execute(self, task, context, session_id, memories):
        self._think(
            content=f"协助处理：{task}",
            node_type="reasoning",
            conf=0.90,
        )

        mem_context = "\n".join([
            _fmt_mem(m) for m in memories[:3]
        ]) if memories else "无相关记忆"

        response = await self._call_llm_web(task, [{
            "role": "user",
            "content": f"请协助处理以下任务：\n{task}\n\n参考记忆：\n{mem_context}"
        }])

        self._think(
            content=f"任务完成：{response[:150]}",
            node_type="decision",
            conf=0.85,
        )

        self._store_memory(
            content=f"协助任务：{task[:100]}\n结果：{response[:200]}",
            mem_type="execution",
        )
        return response, {}


# ══════════════════════════════════════════════════════════
# Agent 3: FinanceAgent — 财务
# ══════════════════════════════════════════════════════════

class FinanceAgent(CognitiveAgent):
    AGENT_ID = "finance"
    AGENT_NAME = "财务 Agent"
    ALLOWED_TOOL_CATEGORIES = {"read", "write", "finance"}

    def attach_runtime(self, runtime):
        """挂载财务内核(FinanceRuntime):记忆+推理+Policy 守门人。"""
        self.fin = runtime

    # ── 编排协作(P3:财务领域态势 + 预算守门人,从编排层下沉至此)──────
    def situation(self) -> dict:
        rt = getattr(self, "fin", None)
        if rt is None:
            return {"domain": "finance", "status": {}, "alerts": []}
        try:
            over = [b for b in rt.reasoning.budget_status(rt.transactions, rt.budgets)
                    if b["is_over"]]
        except Exception:
            over = []
        status = {"transactions": len(rt.transactions),
                  "over_budget_categories": [b["category"] for b in over]}
        alerts = [{"domain": "finance", "severity": "warn",
                   "msg": f"{b['category']} 超支 {b['spent']:.0f}/{b['monthly_limit']:.0f}"}
                  for b in over]
        return {"domain": "finance", "status": status, "alerts": alerts}

    def handle_cross_request(self, req) -> None:
        """差旅/采购预算申请 → 财务守门人**认知化**裁决(v2 ③:观察→假设→证据→裁决,
        规则实现、裁决逐字不变、显式证据链)。"""
        if req.request_type != "budget_request":
            return super().handle_cross_request(req)
        rt = getattr(self, "fin", None)
        amount = req.payload.get("amount", 0)
        category = req.payload.get("category", "travel")
        if rt is None:
            req.status = "approved"; req.verdict = "财务内核未挂载,默认放行"; return
        from ..finance import Transaction
        from ..finance.reasoner import FinanceGatekeeperReasoner
        txn = Transaction(amount=-abs(amount), category=category,
                          entity=req.payload.get("entity", "差旅"),
                          account=req.payload.get("account", "default"))
        bal = rt.account_balances.get(txn.account)
        # Step3:模糊情形的 LLM 裁决(默认关;HESTIA_FINANCE_LLM_ARBITER=1 才启用)。
        # 安全不变量:LLM 只在规则已批准的模糊带被咨询、只能 approve→confirm、出错→confirm、永不 reject。
        import os as _os
        _llm_on = _os.environ.get("HESTIA_FINANCE_LLM_ARBITER", "") == "1"
        rv = FinanceGatekeeperReasoner(
            rt.policy, runtime=rt,
            llm=getattr(self, "router", None), llm_enabled=_llm_on).reason((txn, bal))
        req.status = {"approve": "approved", "confirm": "needs_confirmation",
                      "reject": "rejected"}[rv.decision]
        req.verdict = rv.rationale
        # 附证据链(可审计;新增字段,鸭子类型透传,不改既有行为)
        try:
            req.evidence = [e.to_dict() for e in rv.evidence]
            req.reason_source = rv.source
        except Exception:
            pass

    def analyze_finances(self, period: str = None, current_balance: float = None) -> dict:
        """调用财务内核做综合分析,并把关键洞察写入 ThoughtChain。"""
        fin = getattr(self, "fin", None)
        if fin is None:
            return {"error": "财务内核未挂载"}
        report = fin.analyze(period=period, current_balance=current_balance)
        # 把守门人裁决写进推理链(受 Harness 监督)
        verdicts = report.get("gatekeeper_verdicts", [])
        self._think(
            content=f"财务分析完成:{report['transaction_count']} 笔交易,"
                    f"{len(report.get('anomalies',[]))} 异常,{len(verdicts)} 条守门人裁决",
            node_type="reflection", conf=0.9,
        )
        return report

    @property
    def _role_prompt(self) -> str:
        return """你是 Hestia OS 的财务 Agent，专注于个人财务管理。

你的核心职责：
1. 追踪收入和支出记录
2. 分析消费模式，给出理财建议
3. 制定储蓄和投资计划
4. 提供财务报告和趋势分析

工作原则：
- 所有财务建议都基于用户的历史记录和明确说明的情况
- 保守稳健，不推荐高风险操作
- 尊重用户的财务隐私
- 记住用户的财务目标和偏好，越用越了解

注意：你只能访问财务相关的工具和数据，不能操作删除功能。"""

    _task_entities = lambda self, task: ["财务", "收支", "储蓄"]
    _recall_query = lambda self, task: f"财务 {task}"

    async def _execute(self, task, context, session_id, memories):
            self._think(
                content=f"财务分析：{task}",
                node_type="reasoning",
                conf=0.93,
            )

            # 构建财务上下文
            finance_context = ""
            if memories:
                finance_context = "历史财务记录：\n" + "\n".join([
                    _fmt_mem(m, 100) for m in memories[:5]
                ])

            # 接入财务内核:若已挂载且有交易数据,做真实结构化分析,作为 LLM 的依据
            kernel_insight = ""
            fin = getattr(self, "fin", None)
            if fin is not None and getattr(fin, "transactions", None):
                try:
                    report = fin.analyze()
                    ec = report.get("evidence_chains", [])
                    anomalies = report.get("anomalies", [])
                    parts = []
                    for chain in ec:
                        parts.append(
                            f"· {chain['decision_type']}: 风险{chain['risk_score']}"
                            f"({chain['risk_level']}) — {chain.get('rationale','')};"
                            f"建议: {chain.get('recommended_action','')}")
                    if anomalies:
                        parts.append(f"· 检测到 {len(anomalies)} 笔异常支出")
                    if parts:
                        # 注:标签用产品级措辞(不带"内核"等内部实现词),避免 LLM 回复把内部
                        # 架构词汇透给终端用户(深层实现对用户保密;多 Agent 作卖点可讲)。
                        kernel_insight = ("\n\n【财务分析结果(基于真实交易,请据此回答)】\n"
                                          + "\n".join(parts))
                        # 把证据链摘要沉淀到 ThoughtChain(可审计)
                        self._think(
                            content=f"财务内核证据链:{len(ec)} 条决策,{len(anomalies)} 异常",
                            node_type="reflection", conf=0.9,
                        )
                except Exception:
                    pass   # 内核分析失败不阻断,降级为纯 LLM 回答

            response = await self._call_llm_web(task, [{
                "role": "user",
                "content": f"""财务任务：{task}

{finance_context}{kernel_insight}

请提供专业的财务分析和建议。如果需要记录具体数字，请以结构化格式返回。"""
            }])

            self._think(
                content=f"财务分析完成：{response[:150]}",
                node_type="decision",
                conf=0.90,
            )

            # 记忆财务决策
            self._store_memory(
                content=f"财务分析：{task[:80]}\n结论：{response[:200]}",
                mem_type="decision",
                title=f"财务_{datetime.now().strftime('%Y%m%d')}",
                tags=["finance", "analysis"],
            )
            return response, {}


# ══════════════════════════════════════════════════════════
# Agent 4: AdminAgent — 行政
# ══════════════════════════════════════════════════════════

class AdminAgent(CognitiveAgent):
    AGENT_ID = "admin"
    AGENT_NAME = "行政 Agent"
    ALLOWED_TOOL_CATEGORIES = {"read", "write", "calendar"}

    def attach_runtime(self, runtime):
        """挂载行程内核(ItineraryRuntime):路线优化 + 可达性 + 守门人。"""
        self.itin = runtime

    # ── 编排协作(P3:行程领域态势 + 日程守门人,从编排层下沉至此)──────
    def situation(self) -> dict:
        rt = getattr(self, "itin", None)
        if rt is None:
            return {"domain": "itinerary", "status": {}, "alerts": []}
        try:
            conflicts = rt.reasoning.detect_conflicts(rt.waypoints)
        except Exception:
            conflicts = []
        status = {"waypoints": len(rt.waypoints), "conflicts": len(conflicts)}
        alerts = [{"domain": "itinerary", "severity": "critical", "msg": c["reason"]}
                  for c in conflicts]
        return {"domain": "itinerary", "status": status, "alerts": alerts}

    # 差旅预算让步:每轮降一档;低于原始的 60% 就不再让(那已不是同一趟出差了,
    # 硬压下去只会规划出一个没法执行的行程 —— 宁可认输转由人决定)。
    CONCEDE_STEP = 0.75          # 每轮降到上一轮的 75%(改高铁/换酒店/减一天的量化近似)
    CONCEDE_FLOOR = 0.60         # 相对**原始**申请额的下限

    def concede(self, req, verdict: str):
        """【协商闭环·发起方】差旅预算被财务守门人拒 → 降标准重报。

        只处理自己发起的 budget_request;其余回退默认(不让步)。
        每轮必须**真降**,且有下限 —— 否则会无限贴着守门人的红线来回试探。
        """
        if req.request_type != "budget_request":
            return super().concede(req, verdict)
        try:
            amount = float(req.payload.get("amount") or 0)
        except (TypeError, ValueError):
            return None
        if amount <= 0:
            return None
        original = float(req.payload.get("_original_amount") or amount)
        # 降一档,但**夹到下限**而不是跌破就放弃 —— 否则步长过冲时会在下限之上提前认输
        # (8000→6000→(4500 破线)放弃),白白浪费 4800 这个本来能过的方案。
        new_amount = max(round(amount * self.CONCEDE_STEP), round(original * self.CONCEDE_FLOOR))
        if new_amount <= 0 or new_amount >= amount:
            return None                      # 已到下限,再降就破线 → 认输(维持 rejected,不硬闯)
        p = dict(req.payload)
        p["amount"] = new_amount
        p["_original_amount"] = original
        p["_concession"] = f"降标准:{amount:.0f} → {new_amount:.0f}"
        return p

    def handle_cross_request(self, req) -> None:
        """日程占用申请 → 行程守门人检查冲突。"""
        if req.request_type != "schedule_request":
            return super().handle_cross_request(req)
        rt = getattr(self, "itin", None)
        if rt is None:
            req.status = "approved"; req.verdict = "行程内核未挂载,默认放行"; return
        new_time = req.payload.get("start_time")
        conflict = False
        if new_time:
            from datetime import datetime as _dt
            nt = _dt.fromisoformat(new_time)
            for wp in rt.waypoints:
                if wp.dt and abs((wp.dt - nt).total_seconds()) < 3600:
                    conflict = True
                    break
        if conflict:
            req.status = "needs_confirmation"
            req.verdict = "该时段附近已有行程,可能冲突,需确认"
        else:
            req.status = "approved"
            req.verdict = "时段空闲,日程可安排"

    def plan_itinerary(self, mode: str = "driving") -> dict:
        """调用行程内核做规划,关键洞察写入 ThoughtChain。"""
        rt = getattr(self, "itin", None)
        if rt is None:
            return {"error": "行程内核未挂载"}
        report = rt.plan(mode=mode)
        opt = report.get("optimized_route") or {}
        self._think(
            content=f"行程规划:{report['waypoint_count']} 个点,"
                    f"最优路线 {opt.get('total_km','?')} km / {opt.get('total_min','?')} 分钟,"
                    f"{len(report['conflicts'])} 个冲突,"
                    f"高德={'已接入' if report['amap_enabled'] else '估算模式'}",
            node_type="reflection", conf=0.9,
        )
        return report

    @property
    def _role_prompt(self) -> str:
        return """你是 Hestia OS 的行政 Agent，专注于日程和行程管理。

你的核心职责：
1. 管理日历事件和会议安排
2. 规划出行行程和路线
3. 维护待办事项和提醒
4. 处理行政文档和表格

工作原则：
- 主动记住用户的时间偏好（如不喜欢早 8 点前的会议）
- 提前检测日程冲突
- 整合用户的习惯模式给出智能建议
- 越用越了解用户的生活节奏"""

    _task_entities = lambda self, task: ["日历", "行程", "会议"]
    _recall_query = lambda self, task: f"行程 日程 {task}"

    async def _execute(self, task, context, session_id, memories):
            self._think(
                content=f"行政处理：{task}",
                node_type="reasoning",
                conf=0.91,
            )

            schedule_context = ""
            if memories:
                schedule_context = "历史日程记录：\n" + "\n".join([
                    _fmt_mem(m, 100) for m in memories[:5]
                ])

            # 接入行程内核:若已挂载且有路点,做真实规划(路线/冲突)作为 LLM 依据
            kernel_insight = ""
            itin = getattr(self, "itin", None)
            if itin is not None and getattr(itin, "waypoints", None):
                try:
                    report = itin.plan()
                    opt = report.get("optimized_route") or {}
                    conflicts = report.get("conflicts", [])
                    parts = []
                    if opt:
                        parts.append(f"· 最优路线: {opt.get('total_km','?')}km / "
                                     f"{opt.get('total_min','?')}分钟")
                    if conflicts:
                        parts.append(f"· 检测到 {len(conflicts)} 个日程冲突")
                    if parts:
                        kernel_insight = ("\n\n【行程分析结果(基于真实路点,请据此回答)】\n"
                                          + "\n".join(parts))
                        self._think(
                            content=f"行程内核:路线已优化,{len(conflicts)} 个冲突",
                            node_type="reflection", conf=0.9,
                        )
                except Exception:
                    pass   # 内核失败不阻断,降级纯 LLM

            response = await self._call_llm_web(task, [{
                "role": "user",
                "content": f"""行程任务：{task}

{schedule_context}{kernel_insight}

请处理这个行政/日程任务，给出具体安排建议。"""
            }])

            self._think(
                content=f"行程安排：{response[:150]}",
                node_type="decision",
                conf=0.88,
            )

            self._store_memory(
                content=f"行程任务：{task[:80]}\n安排：{response[:200]}",
                mem_type="execution",
                title=f"日程_{datetime.now().strftime('%Y%m%d')}",
                tags=["admin", "schedule"],
            )
            return response, {}


# ══════════════════════════════════════════════════════════
# Agent 5: OfficeAgent — 办公（学习助理）
# ══════════════════════════════════════════════════════════

class OfficeAgent(CognitiveAgent):
    AGENT_ID = "office"
    AGENT_NAME = "办公 Agent"
    ALLOWED_TOOL_CATEGORIES = {"read", "write", "network"}

    @staticmethod
    def _strip_knowledge_json(text: str) -> str:
        """从回复中剥离 {"new_knowledge": [...]} 结构化块,只保留自然语言。"""
        if not text or "new_knowledge" not in text:
            return text
        import re
        # 去掉 {"new_knowledge": [ ... ]} 整块(含可能的 markdown 代码围栏)
        cleaned = re.sub(r'`{0,3}\s*\{\s*"new_knowledge".*?\}\s*\]\s*\}\s*`{0,3}',
                         '', text, flags=re.DOTALL)
        return cleaned.strip()

    def attach_runtime(self, runtime):
        """挂载学习内核(LearningRuntime):间隔重复 + 知识图谱。"""
        self.learn_rt = runtime

    # ── 编排协作(P3:学习领域态势,从编排层下沉至此)──────
    def situation(self) -> dict:
        rt = getattr(self, "learn_rt", None)
        if rt is None:
            return {"domain": "learning", "status": {}, "alerts": []}
        try:
            states = list(rt.review_states.values())
            due = rt.sr.due_items(states)
            risky = rt.sr.forgetting_risk(states)
        except Exception:
            due = risky = []
        status = {"total_items": len(rt.items), "due_today": len(due), "at_risk": len(risky)}
        alerts = ([{"domain": "learning", "severity": "info",
                    "msg": f"{len(risky)} 个知识点高遗忘风险待复习"}] if risky else [])
        return {"domain": "learning", "status": status, "alerts": alerts}

    def analyze_learning(self) -> dict:
        """调用学习内核做综合分析,关键洞察写入 ThoughtChain。"""
        rt = getattr(self, "learn_rt", None)
        if rt is None:
            return {"error": "学习内核未挂载"}
        report = rt.analyze()
        # 给前端「知识库」轮播提供知识条目明细:analyze 本身只有统计、无明细,
        # 否则前端读 analysis.items 拿不到 → 一直显示"知识库为空"(即便已录入)。
        try:
            with rt._lock:
                _vals = list(rt.items.values())
            report["items"] = [
                {"title": (it.content or "")[:40], "content": it.content,
                 "topic": it.topic, "tag": it.topic, "source": it.source,
                 "tags": it.tags, "id": it.id, "notes": getattr(it, "notes", [])}
                for it in reversed(_vals)
            ][:30]
        except Exception:
            report.setdefault("items", [])
        self._think(
            content=f"学习分析:{report['total_items']} 个知识点,"
                    f"平均掌握度 {report['avg_mastery']},"
                    f"{report['due_today']} 个待复习,"
                    f"图谱 {report['graph']['clusters']} 个知识簇",
            node_type="reflection", conf=0.9,
        )
        return report

    @property
    def _role_prompt(self) -> str:
        return """你是 Hestia OS 的办公 Agent，专注于学习和知识管理。

你的核心职责：
1. 帮助用户学习新知识和技能
2. 管理知识储备，建立个人知识库
3. 处理文档、报告和写作任务
4. 跟踪学习进度和知识演化

Hermes 自学原则（像赫尔墨斯那样）：
- 不是学习更多技能，而是通过每次交互变得更"智能"
- 记住用户的学习风格和偏好
- 识别知识缺口，主动补充
- 建立知识间的连接，而不只是存储孤立事实
- 每次对话都让知识库更丰富、更有组织

你的知识是活的，会生长的。"""

    _task_entities = lambda self, task: ["学习", "知识", "文档"]
    _recall_topk = 8   # 办公 Agent 广泛召回

    async def _execute(self, task, context, session_id, memories):
            self._think(
                content=f"知识任务：{task}，已有相关记忆：{len(memories)} 条",
                node_type="reasoning",
                conf=0.92,
            )

            knowledge_context = ""
            if memories:
                knowledge_context = "相关知识储备：\n" + "\n".join([
                    f"- {_fmt_mem(m, 80)}" for m in memories[:6]
                ])

            # 接入学习内核:若已挂载且有知识点,做真实分析(待复习/掌握度)作为依据
            kernel_insight = ""
            learn_rt = getattr(self, "learn_rt", None)
            if learn_rt is not None and getattr(learn_rt, "items", None):
                try:
                    report = learn_rt.analyze()
                    due = report.get("due_today", 0)
                    mastery = report.get("avg_mastery", 0)
                    kernel_insight = (
                        f"\n\n【学习状态(基于真实知识库)】\n"
                        f"· 知识点总数: {report.get('total_items', 0)}\n"
                        f"· 平均掌握度: {mastery}\n"
                        f"· 今日待复习: {due} 个")
                    self._think(
                        content=f"学习内核:{report.get('total_items',0)} 知识点,{due} 待复习",
                        node_type="reflection", conf=0.9,
                    )
                except Exception:
                    pass   # 内核失败不阻断,降级纯 LLM

            response = await self._call_llm_web(task, [{
                "role": "user",
                "content": f"""学习任务：{task}

{knowledge_context}{kernel_insight}

请：
1. 帮助完成这个学习/办公任务
2. 识别涉及的关键知识点
3. 指出哪些是新知识（需要记录）

回答后，以 JSON 格式附上需要记录的新知识：
{{"new_knowledge": [{{"title": "知识标题", "content": "知识内容", "tags": ["标签"]}}]}}"""
            }])

            # 提取并存储新知识(括号平衡解析:正确处理内容含 []{} 的情况)
            new_knowledge_count = 0
            try:
                start = response.find('{"new_knowledge"')
                if start < 0:
                    start = response.find('{ "new_knowledge"')
                if start >= 0:
                    # 从 start 起做花括号配对,找到完整 JSON 对象的结束位置
                    depth, end, in_str, esc = 0, -1, False, False
                    for i in range(start, len(response)):
                        c = response[i]
                        if esc:
                            esc = False; continue
                        if c == '\\':
                            esc = True; continue
                        if c == '"':
                            in_str = not in_str
                        elif not in_str:
                            if c == '{':
                                depth += 1
                            elif c == '}':
                                depth -= 1
                                if depth == 0:
                                    end = i + 1; break
                    if end > start:
                        knowledge_data = json.loads(response[start:end])
                        for k in knowledge_data.get("new_knowledge", []):
                            if not isinstance(k, dict) or not k.get("content"):
                                continue
                            self._store_memory(
                                content=k["content"],
                                mem_type="concept",
                                title=k.get("title", "未命名知识"),
                                tags=k.get("tags", ["office", "learning"]),
                            )
                            new_knowledge_count += 1
            except Exception:
                pass  # 解析失败不影响主流程

            # 从返回文本中剥离 new_knowledge JSON 块(不展示给用户)
            response = self._strip_knowledge_json(response)

            self._think(
                content=f"学习完成，新增知识 {new_knowledge_count} 条",
                node_type="reflection",
                conf=0.90,
            )
            return response, {"new_knowledge_stored": new_knowledge_count}
