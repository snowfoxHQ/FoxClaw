"""
主管专用 pipeline stages(P1-1)—— 把 SupervisorAgent 原 `_run_task_body` 的路由分发逻辑
拆成可组合、可观测、可中断的 stage,使主管改走 base 统一模板 `_run_task_core`,从而获得
hooks / 流式(run_task_stream)/ stop_event 中断 / 统一 `_emit_execution` / blocked 顶层提升。

设计约束(与文稿 P1-1 规格 + 现行为逐点对齐):
  · stage 是操作 ctx 的可调用(async 函数 + stage_name 属性),与 cognitive_pipeline 既有约定一致;
  · 只操作 ctx 与 agent 的既有方法(_route/_schedule/_plan_task/_dispatch_*),不新增语义;
  · 分诊 prompt / 自答 prompt / 调度 think 文案全部**逐字搬移**自 agents.py 原实现,不改行为;
  · 主管自答(无关键词命中+模型可用)是 route 的**决策**、dispatch 的**执行**——决策与执行分 stage,
    让 stop_event 在"决定自答"与"花钱调 LLM"之间有检查点;
  · execution.completed 不再由 dispatch 手动 publish(改由 base 模板 `_emit_execution` 统一发,
    文稿 P1-1 Step 4);无总线时的手动兜底沉淀(_store_memory)保留(standalone/测试场景);
  · 内部短路标记放 ctx.extra 的 `_meta_answered`/`_self_answer`,aggregate 负责清除,不外泄到返回体。
"""
import asyncio
from datetime import datetime

from ..core.dispatch import dispatch_failed  # 谓词在 core:避免 pipeline 反向 import agents
from .cognitive_pipeline import PipelineContext, stage_recall  # noqa: F401  (recall 复用默认 stage)
from .stage_meta import named_stage


# ── Stage 2: meta_check —— 元问题判断(你是谁/能做什么 → 主管以总管身份自答,不分派)──
@named_stage("meta_check")
async def sv_meta_check(ctx: PipelineContext) -> None:
    agent = ctx.agent
    if not agent._is_meta(ctx.task):
        return
    agent._think(
        content=f"元问题「{ctx.task}」由主管直接回答(系统介绍,不分派)",
        node_type="decision", conf=0.92,
    )
    subagents = getattr(agent, "_subagents", {})
    # 拓扑 5 → 3:助手/行政已下放为指令技能,不再出现在团队介绍里。
    sub_names = {
        "finance": "财务 Agent(理财、收支、预算、风控)",
        "office": "办公学习 Agent(学习、知识管理、研究、写作、日程与行程)",
    }
    available = [v for k, v in sub_names.items() if k in subagents]
    team = "\n".join(f"· {x}" for x in available) if available else "(子 Agent 尚未注册)"
    intro_prompt = (
        f"你是 Foxclaw 的主管 Agent(Supervisor),负责统筹协调整个智能体团队。"
        f"用户问:「{ctx.task}」。请以主管身份友好、简洁地直接回答用户这个问题。\n"
        f"背景信息(供你回答时参考):\n"
        f"- 你统领的团队:\n{team}\n"
        f"- 关于联网:Foxclaw 已接入百度 AI 搜索,具备联网查询实时信息的能力"
        f"(当用户问最新版本、新闻、价格等时会自动联网)。所以如果用户问你能不能联网,"
        f"应回答「可以联网」,而不是说没有联网能力。\n"
        f"请针对用户的实际问题回答,控制在 150 字内,口吻像专业总管,不要自称助手,"
        f"不要答非所问地只做自我介绍。"
    )
    reply = await agent._call_llm([{"role": "user", "content": intro_prompt}])
    agent._store_memory(
        content=f"主管自答元问题「{ctx.task}」",
        mem_type="decision",
        title=f"主管介绍_{datetime.now().strftime('%Y%m%d_%H%M')}",
        shared=True,
    )
    ctx.result = {"dispatched_to": "supervisor", "sub_result": reply,
                  "self_answered": True}
    ctx.extra["_meta_answered"] = True      # 短路标记:后续 stage 跳过(aggregate 清除)


# ── Stage 3: schedule —— 认知调度(优先级/负载/抢占)────────────────
@named_stage("schedule")
async def sv_schedule(ctx: PipelineContext) -> None:
    if ctx.extra.get("_meta_answered"):
        return
    agent = ctx.agent
    subagents = getattr(agent, "_subagents", {})
    ctx.sched = agent._schedule(ctx.task, subagents)
    agent._think(
        content=(f"认知调度:{ctx.sched.reason}"
                 + (f" · 抢占({ctx.sched.preemption_reason})" if ctx.sched.preemptible else "")),
        node_type="decision", conf=0.9,
    )


# ── Stage 4: route —— 路由决策(关键词+调度意图;未命中且模型可用 → 决策主管自答)──
@named_stage("route")
async def sv_route(ctx: PipelineContext) -> None:
    if ctx.extra.get("_meta_answered"):
        return
    agent = ctx.agent
    target = agent._route(ctx.task, ctx.sched)
    ctx.target = target
    ctx.route = {"target": target, "reason": "主管关键词+调度意图分诊"}
    # 无关键词命中 = 正面对话/泛化问题 → 主管**直接自答一次**,省掉"LLM 分诊 + 派发"的
    # 第二次串行 LLM 调用(每次约 5s 网络往返,正面对话不值当)。只有命中领域关键词
    # (财务/行政/办公/…)才派发给对应子 Agent。stub 下保持原路径(零额外调用、离线测试确定)。
    # 权衡:少数本该派给领域 Agent 的"无关键词"任务会由主管自答——用户明确选速度优先。
    # (本 stage 只做**决策**置标记;自答的 LLM 调用在 dispatch stage 执行。)
    if not agent._has_keyword_hit(ctx.task) and not agent.router.is_stub:
        agent._think(
            content="无关键词命中 → 主管直接自答(省掉二次分诊派发,正面对话提速)",
            node_type="decision", conf=0.88)
        ctx.extra["_self_answer"] = True
        return
    agent._think(
        content=f"任务分诊:「{ctx.task}」→ 派给 {target} Agent。已有相关记忆 {len(ctx.memories)} 条",
        node_type="decision",
        conf=0.9,
    )


# ── Stage 5: plan —— planner 拆解(多步?跨 Agent?)────────────────
@named_stage("plan")
async def sv_plan(ctx: PipelineContext) -> None:
    if ctx.extra.get("_meta_answered") or ctx.extra.get("_self_answer"):
        return
    agent = ctx.agent
    # P0-1:LLM 规划含同步网络调用(call_tools),放线程池避免阻塞事件循环;
    # 规则拆解路径是纯 CPU,放线程池开销可忽略、行为不变。
    plan_obj, step_routes = await asyncio.to_thread(agent._plan_task, ctx.task, ctx.sched)
    if plan_obj is not None:
        routes_desc = " → ".join(f"{name}[{ag}]" for name, ag in step_routes)
        agent._think(
            content=f"planner 判定为多步任务({len(plan_obj.tasks)}步):{routes_desc}",
            node_type="decision", conf=0.85,
        )
        # 若子任务跨不同 Agent,标记为多 Agent 顺序执行
        distinct_agents = {ag for _, ag in step_routes}
        ctx.multi_agent = len(distinct_agents) > 1
    else:
        ctx.multi_agent = False
    ctx.plan_obj = plan_obj
    ctx.step_routes = step_routes or []


# ── Stage 6: dispatch —— 分发执行(自答 / 多 Agent / 单 Agent / 兜底)──
@named_stage("dispatch")
async def sv_dispatch(ctx: PipelineContext) -> None:
    agent = ctx.agent
    if ctx.extra.get("_meta_answered"):
        return

    # 主管自答(route stage 的决策在此执行)
    if ctx.extra.get("_self_answer"):
        _msgs = [{"role": "user", "content": (
            f"你是 Foxclaw 的主管 Agent(Supervisor),统筹整个智能体团队。"
            f"用户说:「{ctx.task}」。请以主管身份**直接、友好、专业**地回答,针对用户的实际"
            f"问题作答,不要只做自我介绍、不要答非所问。若确属需要专门领域深办的任务,可"
            f"简要引导用户去对应能力,但仍先给出有用的直接回应。"
        )}]
        # 联网增强:新闻/实时类(_web_augment 内 needs_search 命中)走百度搜索注入真实结果
        # 再作答,避免主管凭空编造(系统 prompt 明令严禁模拟搜索)。纯闲聊 needs_search=False,
        # 不搜、不减速。阻塞 I/O 放线程池(与 base._execute 同法),不堵 async 事件循环。
        _msgs = await asyncio.to_thread(agent._web_augment, ctx.task, _msgs)
        reply = await agent._call_llm(_msgs)
        agent._store_memory(
            content=f"主管自答正面对话「{ctx.task}」", mem_type="decision",
            title=f"主管自答_{datetime.now().strftime('%Y%m%d_%H%M')}", shared=True)
        ctx.dispatch_result = {
            "result": {"dispatched_to": "supervisor", "sub_result": reply,
                       "self_answered": True},
        }
        return

    subagents = getattr(agent, "_subagents", {})

    # 多 Agent 执行:并行计划(无依赖)→ fork-join 并发;否则顺序执行后汇聚
    if ctx.multi_agent and ctx.step_routes:
        if getattr(ctx.plan_obj, "flow_type", "sequential") == "parallel":
            agent._think(
                content=f"计划为并行({len(ctx.step_routes)}个无依赖子任务)→ fork-join 并发调度",
                node_type="decision", conf=0.88,
            )
            ctx.dispatch_result = await agent._dispatch_parallel(
                ctx.task, ctx.step_routes, ctx.sched, ctx.session_id,
                subagents, ctx.context, ctx.memories)
        else:
            ctx.dispatch_result = await agent._dispatch_multi(
                ctx.task, ctx.step_routes, ctx.sched, ctx.session_id,
                subagents, ctx.context, ctx.memories)
        return

    # 单 Agent 分发
    if ctx.target in subagents:
        ctx.dispatch_result = await agent._dispatch_single(
            ctx.task, ctx.target, ctx.sched, ctx.session_id,
            subagents, ctx.context, ctx.memories, ctx.plan_obj)
        return

    # 子 Agent 不可用(未注册)→ 主管自处理
    ctx.dispatch_result = await agent._dispatch_fallback(ctx.task, ctx.context)


# ── Stage 6.5: replan —— 反思重规划(P0-1:失败步骤 → LLM 反思 → 修正计划 → 重执行)──
@named_stage("replan")
async def sv_replan(ctx: PipelineContext) -> None:
    if ctx.extra.get("_meta_answered") or ctx.extra.get("_self_answer"):
        return
    agent = ctx.agent
    dr = ctx.dispatch_result or {}
    if not dispatch_failed(dr):
        return                      # 无失败/未完成步骤 → 无需重规划
    if not agent._replan_enabled():
        return                      # opt-in 关 / 不支持 tools → 保留原结果(fail-safe)
    subagents = getattr(agent, "_subagents", {})
    max_rounds = int((ctx.context or {}).get("replan_max_rounds", 2))
    ctx.dispatch_result = await agent._replan_and_redo(
        ctx.task, ctx.sched, ctx.session_id, subagents, ctx.context, ctx.memories,
        dr, max_rounds=max_rounds)


# ── Stage 7: aggregate —— 结果组装(dispatch_result → ctx.result/extra)──
@named_stage("aggregate")
async def sv_aggregate(ctx: PipelineContext) -> None:
    # 清除内部短路标记,不外泄到返回体(它们会经 **ctx.extra 透传进 resp)
    ctx.extra.pop("_meta_answered", None)
    ctx.extra.pop("_self_answer", None)
    dr = ctx.dispatch_result or {}
    if not dr:
        return                      # meta 路径:result 已在 meta_check 设好
    ctx.result = dr.get("result", ctx.result)
    # 白名单透传(与原主管返回体逐字一致的顶层键);session_id/agent/thought_summary
    # 由 base 模板统一组装,不从 dispatch_result 取。
    for k in ("schedule", "plan", "process_id"):
        if k in dr:
            ctx.extra[k] = dr[k]
    # 「未完成 ⇒ 不作为已完成真值沉淀」:任一子任务被 Harness 拦截 → 提升为顶层
    # status=blocked(与 base 模板的 skill_status 提升同一机制),执行事件 blocked=True,
    # 真值链不沉淀(呼应 P3-5)。成功/自答路径不带此键,行为逐字不变。
    if isinstance(ctx.result, dict) and ctx.result.get("blocked"):
        ctx.extra["skill_status"] = "blocked"


# 主管 pipeline 的 stage 序列(recall 复用默认实现:与旧 `self._recall_memory(task)` 逐字等价)
SUPERVISOR_STAGES = [stage_recall, sv_meta_check, sv_schedule, sv_route,
                     sv_plan, sv_dispatch, sv_replan, sv_aggregate]
