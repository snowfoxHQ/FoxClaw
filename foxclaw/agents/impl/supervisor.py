"""SupervisorAgent —— 主管（总协调、任务分发、全局态势）。"""

import asyncio
import json
import time
from datetime import datetime

from ..base import CognitiveAgent
from ...planning import planner as P
from ._helpers import _with_prior, _dispatch_failed, _extract_step_results, PRIOR_MAX_CHARS

class SupervisorAgent(CognitiveAgent):
    AGENT_ID = "supervisor"
    AGENT_NAME = "主管 Agent"
    ALLOWED_TOOL_CATEGORIES = {"read", "write", "calendar", "finance", "network"}
    IS_ROUTER = True  # 调度层:豁免语义漂移拦截(接收任意输入是其本职)

    @property
    def _role_prompt(self) -> str:
        return """你是 Foxclaw 的主管 Agent，负责总体协调和任务分发。

你的核心职责：
1. 理解用户的整体目标，将复杂任务分解为子任务
2. 根据任务性质，决定分配给哪个子 Agent（财务 / 办公学习）
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
        # 本方法(Foxclaw 启动时单线程调一次)**预建**,并发任务到达时属性已就绪、不再 race。
        # 保留下游懒建作 standalone(未 register)兜底。
        from ...core.scheduler import CognitiveScheduler
        from ...core.steering_queue import SteeringQueue
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
        from foxclaw.orchestration import CrossAgentRequest
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

    # 路由关键词表:无 LLM 时也能基于关键词分发。
    # 拓扑 5 → 3 后:原行政(日程/行程)关键词并入办公;"助手"那组(整理/汇总/沟通)
    # 不再有归属 —— 它们正是主管自己该干的兜底活,未命中 → 主管自答。
    ROUTING_KEYWORDS = {
        "finance": ["收支", "支出", "预算", "理财", "投资", "储蓄", "财务", "消费", "报销", "账"],
        "office":  ["学习", "论文", "知识", "总结", "研究", "文档", "写作", "资料", "搜索", "查",
                    "日程", "行程", "会议", "出差", "预约", "日历", "计划表", "提醒",
                    # 原助手的"整理/汇总/邮件"归办公:那是内容活,主管只兜底通用与协调
                    "整理", "汇总", "邮件", "纪要"],
    }

    # 子 Agent 职责描述(P0-1 LLM 规划用):喂给 submit_plan 工具做 agent 枚举约束
    SUBAGENT_DESC = {
        "finance": "财务 Agent：理财、收支、预算、风控、对账",
        "office": "办公学习 Agent：学习、知识管理、研究、写作、日程与行程安排",
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

    @staticmethod
    def _is_expense_report(task: str) -> bool:
        """是不是一句「零星花钱报备」(今天买衣服 105 块 / 后天洗车 100)。

        这类句子**不含任何财务关键词**,但它是财务应用最前线的输入。不在这里拦一道,
        就会被当成闲聊由主管自答,账直接丢了 —— 所以它必须算作"命中财务领域"。
        """
        try:
            from ...finance.nlparse import has_expense_signal
            return has_expense_signal(task)
        except Exception:
            return False

    def _route(self, task: str, sched=None) -> str:
        """决定任务派给哪个子 Agent。

        综合决策:花费报备(最高优先)+ 关键词命中(主)+ 调度意图(辅)。
        - 花费报备 → 财务(哪怕一个财务关键词都没出现)
        - 关键词明确命中 → 直接采用(领域词最可靠)
        - 关键词不明 + 调度意图为 memory/reasoning → 倾向 office(知识/研究)
        - 都不明 → "supervisor"(主管自答;助手已下放为技能,不再有兜底 Agent)
        """
        if self._is_expense_report(task):
            return "finance"
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
        # 无明确归属 → 主管自答(dispatch 见 target 不在 subagents 即走 _dispatch_fallback)
        return "supervisor"

    def _coerce_agent(self, ag: str, subtask: str, subagents: dict) -> str:
        """把 planner 给出的未知 agent 名收敛到一个真实存在的子 Agent。

        拓扑收缩后旧名字(assistant/admin)可能仍出现在 LLM 的计划里 —— 直接按子任务重新
        分诊一次;仍不命中就交给"办公学习"(通用性最强),绝不留一个 `subagents[ag]` 会
        KeyError 的名字。"""
        re_routed = self._route(subtask)
        if re_routed in subagents:
            return re_routed
        return "office" if "office" in subagents else next(iter(subagents), "")

    def _has_keyword_hit(self, task: str) -> bool:
        """任务是否命中任一领域关键词(命中 = 关键词路由可信,不必再问 LLM)。

        花费报备也算命中 —— 否则 route stage 会把「今天买衣服 105 块」判成
        "无关键词 → 主管自答",连派发都进不去。
        """
        return (any(P.keyword_hits(task, kws) for kws in self.ROUTING_KEYWORDS.values())
                or self._is_expense_report(task))

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

        【P0-1】FOXCLAW_LLM_PLAN=1 时优先 LLM 规划(function-calling 拆解+路由);
        不可用/失败/单步 → 回退下方规则拆解(行为不变)。
        """
        llm = self._llm_plan(task)
        if llm is not None:
            return llm
        try:
            from ...skills.pipeline import plan as ahs_plan
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
        from ...planning.llm_planner import llm_plan, llm_plan_enabled
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
        from ...skills.pipeline import Plan, PlanTask
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
        from ...planning.llm_planner import replan_enabled
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
        from ...planning.llm_planner import llm_replan
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
        from ...core.scheduler import CognitiveScheduler, SystemContext
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
                ag = self._coerce_agent(ag, subtask, subagents)
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
        from ...core.steering_queue import SteeringQueue
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
                ag = self._coerce_agent(ag, subtask, subagents)
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
    # (foxclaw/pipeline/supervisor_stages.py:recall→meta_check→schedule→route→plan→
    # dispatch→aggregate)。本类只保留:pipeline 覆盖、领域钩子、恢复记账、分发执行体。

    def _cognitive_pipeline(self):
        """主管的认知管线:SUPERVISOR_STAGES(路由分发),而非领域 Agent 的 DEFAULT_STAGES。"""
        pipe = getattr(self, "_pipeline_cache", None)
        if pipe is None:
            from ...pipeline import CognitivePipeline
            from ...pipeline.supervisor_stages import SUPERVISOR_STAGES
            pipe = self._pipeline_cache = CognitivePipeline(SUPERVISOR_STAGES)
        return pipe

    def _task_entities(self, task: str) -> list:
        """主管的目标基线实体(旧 run_task 用 _extract_entities 开会话,逐字保留)。"""
        return self._extract_entities(task)

    # 崩溃/重启恢复记账已上移到 base 生命周期钩子(领域 Agent 此前完全没接线,跑一半崩掉
    # 在恢复扫描里不可见);主管只是"恰好也装着 _recovery",无需再覆盖。

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

