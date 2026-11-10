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

from ..core.dispatch import dispatch_failed
from .cognitive_pipeline import PipelineContext, stage_recall  # noqa: F401  (recall 复用默认 stage)
from .stage_meta import named_stage


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
    ctx.extra["_meta_answered"] = True


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


@named_stage("route")
async def sv_route(ctx: PipelineContext) -> None:
    if ctx.extra.get("_meta_answered"):
        return
    agent = ctx.agent
    target = agent._route(ctx.task, ctx.sched)
    ctx.target = target
    ctx.route = {"target": target, "reason": "主管关键词+调度意图分诊"}
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


@named_stage("plan")
async def sv_plan(ctx: PipelineContext) -> None:
    if ctx.extra.get("_meta_answered") or ctx.extra.get("_self_answer"):
        return
    agent = ctx.agent
    plan_obj, step_routes = await asyncio.to_thread(agent._plan_task, ctx.task, ctx.sched)
    if plan_obj is not None:
        routes_desc = " → ".join(f"{name}[{ag}]" for name, ag in step_routes)
        agent._think(
            content=f"planner 判定为多步任务({len(plan_obj.tasks)}步):{routes_desc}",
            node_type="decision", conf=0.85,
        )
        distinct_agents = {ag for _, ag in step_routes}
        ctx.multi_agent = len(distinct_agents) > 1
    else:
        ctx.multi_agent = False
    ctx.plan_obj = plan_obj
    ctx.step_routes = step_routes or []


@named_stage("dispatch")
async def sv_dispatch(ctx: PipelineContext) -> None:
    agent = ctx.agent
    if ctx.extra.get("_meta_answered"):
        return

    if ctx.extra.get("_self_answer"):
        _msgs = [{"role": "user", "content": (
            f"你是 Foxclaw 的主管 Agent(Supervisor),统筹整个智能体团队。"
            f"用户说:「{ctx.task}」。请以主管身份**直接、友好、专业**地回答,针对用户的实际"
            f"问题作答,不要只做自我介绍、不要答非所问。若确属需要专门领域深办的任务,可"
            f"简要引导用户去对应能力,但仍先给出有用的直接回应。"
        )}]
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

    if ctx.target in subagents:
        ctx.dispatch_result = await agent._dispatch_single(
            ctx.task, ctx.target, ctx.sched, ctx.session_id,
            subagents, ctx.context, ctx.memories, ctx.plan_obj)
        return

    ctx.dispatch_result = await agent._dispatch_fallback(ctx.task, ctx.context)


@named_stage("replan")
async def sv_replan(ctx: PipelineContext) -> None:
    if ctx.extra.get("_meta_answered") or ctx.extra.get("_self_answer"):
        return
    agent = ctx.agent
    dr = ctx.dispatch_result or {}
    if not dispatch_failed(dr):
        return
    if not agent._replan_enabled():
        return
    subagents = getattr(agent, "_subagents", {})
    max_rounds = int((ctx.context or {}).get("replan_max_rounds", 2))
    ctx.dispatch_result = await agent._replan_and_redo(
        ctx.task, ctx.sched, ctx.session_id, subagents, ctx.context, ctx.memories,
        dr, max_rounds=max_rounds)


@named_stage("aggregate")
async def sv_aggregate(ctx: PipelineContext) -> None:
    ctx.extra.pop("_meta_answered", None)
    ctx.extra.pop("_self_answer", None)
    dr = ctx.dispatch_result or {}
    if not dr:
        return
    ctx.result = dr.get("result", ctx.result)
    for k in ("schedule", "plan", "process_id"):
        if k in dr:
            ctx.extra[k] = dr[k]
    if isinstance(ctx.result, dict) and ctx.result.get("blocked"):
        ctx.extra["skill_status"] = "blocked"


SUPERVISOR_STAGES = [stage_recall, sv_meta_check, sv_schedule, sv_route,
                     sv_plan, sv_dispatch, sv_replan, sv_aggregate]
