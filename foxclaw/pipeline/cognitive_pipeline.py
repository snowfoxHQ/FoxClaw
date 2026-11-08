"""
CognitivePipeline —— 显式分阶段认知管线(v2 ② Step1:抽壳)。

目标形态:
    perceive → recall → plan → route → act → verify → reflect → persist
每个 stage 接收并就地更新共享的 PipelineContext(同步或异步均可)。
管线本身只负责"按序跑 stage",不含任何领域/编排逻辑——那些在各 stage 内。

Step 1 只实现 recall / act 两个 stage(等价于 P1-1 模板的召回+领域执行)。
异常(如 HarnessBlockedError)向上抛出,由 base.run_task 统一兜底/emit——保持 P1-1 行为。
"""
from __future__ import annotations
import asyncio
import inspect
import os
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class PipelineContext:
    """一次任务在管线里流动的共享上下文。stage 读它、也写它。

    【P4 审核·可变但并发安全,2026-07-17 V3】ctx 是 run_task 内**每任务新建的局部对象**
    (base.run_task:`ctx = PipelineContext(...)`),且 CognitivePipeline.run 里 stage
    **顺序 await、无 stage 并行**——故 ctx **不跨任务共享**。范式原则五"不可变状态流转"
    反对的是"跨任务/并发共享可变状态"(改一半被读到不一致);ctx 不属于该反模式,可变安全。
    并发隔离由 test_pipeline.py::test_concurrent_ctx_isolation 锁定(24 并发任务零串)。
    (会话指针 _session_id/_parent_node_id 另经 contextvar 隔离,见 base.py。)"""
    agent: Any                      # 执行本任务的 Agent(CognitiveAgent)
    task: str
    context: dict = field(default_factory=dict)
    session_id: str = ""
    # 各 stage 逐步填充的产物
    perceived: dict = field(default_factory=dict)   # perceive stage 的输入理解
    memories: list = field(default_factory=list)
    plan: dict = field(default_factory=dict)        # plan stage 的任务结构(多步?并行?)
    route: dict = field(default_factory=dict)       # route stage 的路由决策(target)
    result: Any = None
    extra: dict = field(default_factory=dict)
    verified: dict = field(default_factory=dict)   # verify stage 汇总的 Harness 校验结果
    reflect: dict = field(default_factory=dict)     # reflect stage 的决策(accept/retry + 次数)
    trace: list = field(default_factory=list)   # 逐 stage 轨迹(后续步骤 emit 用)
    # ── 协作式中断(P3,opt-in)──────────────────────────────
    stop_event: Any = None      # 置位则在下一 stage 边界 / reflect 重试迭代前优雅停止;默认 None=永不停
    interrupted: bool = False   # 被 stop_event 提前中断(供调用方识别"部分完成、未跑完全部 stage")
    # ── 【P1-1】主管路由专用字段(领域 Agent 不用,默认空)──────────
    # 主管 pipeline(supervisor_stages.SUPERVISOR_STAGES)在 schedule/route/plan/dispatch
    # 各 stage 间经这些字段传递路由决策;领域 Agent 的 DEFAULT_STAGES 从不读它们。
    sched: Any = None                       # schedule stage 的 ScheduleDecision
    target: str = ""                        # route stage 的分诊目标(子 Agent id)
    plan_obj: Any = None                    # plan stage 的 planner 拆解结果(多步计划)
    step_routes: list = field(default_factory=list)   # [(子任务文本, 目标 Agent)]
    multi_agent: bool = False               # 是否跨多 Agent(顺序链式 / 并行 fork-join)
    dispatch_result: dict = field(default_factory=dict)  # dispatch stage 的原始分发返回


def _emit_stage(ctx: "PipelineContext", stage_name: str) -> None:
    """逐 stage emit `agent.cognition.<stage>` 到事件脊柱(Step 5:可观测/可重放)。
    经 ctx.agent._caps 解析脊柱;无中枢(standalone/假 Agent)则优雅跳过。
    payload 刻意极小(只 agent/session/stage)——这类高频事件给重放/审计用,
    WS 投影会把 agent.cognition.* 过滤掉不广播(避免泛洪,见 api._register_spine_projection)。"""
    agent = ctx.agent
    caps = getattr(agent, "_caps", None)
    spine = caps.get("event_spine") if caps is not None else None
    if spine is None:
        return
    try:
        spine.publish(f"agent.cognition.{stage_name}", {
            "agent_id": getattr(agent, "AGENT_ID", "?"),
            "session_id": ctx.session_id,
            "stage": stage_name,
        }, source=getattr(agent, "AGENT_ID", "pipeline"))
    except Exception:
        pass


def _stop_requested(ctx: "PipelineContext") -> bool:
    """协作式中断检查点(P3)。stop_event 置位 → 请求在下一个 stage 边界 / 重试迭代前停止。
    duck-type 于 `.is_set()`(threading.Event 与 asyncio.Event 皆可,与 stream_reply→model_router
    的 stop_event **同一约定**)。默认 `ctx.stop_event=None` → 恒 False → 行为逐字不变(opt-in)。"""
    ev = ctx.stop_event
    return ev is not None and ev.is_set()


class CognitivePipeline:
    """有序 stage 列表。run(ctx) 按序执行,支持同步/异步 stage,逐 stage 记轨迹 + emit 事件。"""

    def __init__(self, stages: list[Callable]):
        self.stages = list(stages)

    async def run(self, ctx: PipelineContext, on_stage=None) -> PipelineContext:
        """on_stage(name, ctx)(D1,opt-in):每个 stage 完成后回调一次,供流式路径
        (`run_task_stream`)逐 stage 吐进度。默认 None → 不调用,行为逐字不变。
        回调异常被吞(fail-safe):流式反馈绝不拖垮任务执行。"""
        from foxclaw.core.tracing import span as _span
        for stage in self.stages:
            # 协作式中断点(P3):stage **边界**检查 stop_event。opt-in——默认 stop_event=None 恒不触发,
            # 与改前逐字一致。置位时优雅停:标记 interrupted、保留已完成 stage 的 ctx 产物、不跑后续 stage。
            # 粒度=stage 边界:一般无法中止已在执行的单个 stage;但**流式路径(run_task_stream,D1)**下
            # act 的 LLM 生成经 `_act_stream` 的**同一** stop_event 串到 `router.stream`,可 **token 级**
            # 即时停 → 断连时二者协同:停后续 stage(此处)+ 停 act 生成(base._call_llm),省 token。
            if _stop_requested(ctx):
                ctx.interrupted = True
                break
            name = getattr(stage, "stage_name", getattr(stage, "__name__", "stage"))
            # 单一 stage 循环 → 一处埋点覆盖全部 stage(链路里能看到 perceive→…→reflect 各自耗时)
            with _span(f"stage.{name}", agent=getattr(ctx.agent, "AGENT_ID", "")):
                r = stage(ctx)
                if inspect.isawaitable(r):
                    await r
            ctx.trace.append(name)
            _emit_stage(ctx, name)
            if on_stage is not None:
                try:
                    on_stage(name, ctx)         # D1:逐 stage 吐进度给流式路径
                except Exception:
                    pass                        # 流式回调故障不拖垮任务执行(fail-safe)
        return ctx


# ── Stages(Step 1:recall / act)──────────────────────────────
# stage 是操作 ctx 的可调用;命名 stage_* 便于事件与轨迹标注。

def stage_perceive(ctx: PipelineContext) -> None:
    """感知:把原始输入框定为结构化感知(Step 5)。当前记录轻量输入特征;
    这里是未来"意图识别/输入解析"的落点。也是逐 stage 事件链的起点。"""
    ctx.perceived = {
        "task_len": len(ctx.task or ""),
        "has_context": bool(ctx.context),
    }
stage_perceive.stage_name = "perceive"


async def stage_recall(ctx: PipelineContext) -> None:
    """召回相关记忆(等价 P1-1 模板的 _recall_memory(_recall_query, top_k))。"""
    a = ctx.agent
    ctx.memories = a._recall_memory(a._recall_query(ctx.task), top_k=a._recall_topk)
stage_recall.stage_name = "recall"


def _plan_text(plan: dict) -> str:
    steps = plan.get("steps") or []
    body = " → ".join(f"{i + 1}){s}" for i, s in enumerate(steps))
    return f"计划[{plan.get('flow', 'single')}/{len(steps)}步]:{body}"[:400]


def _record_plan_node(ctx: PipelineContext) -> None:
    """把计划落成 thought-chain 的 `plan` 节点(steps/flow 进 metadata),供回显与审计。

    纯观测、fail-safe:任何失败(含 Harness 对计划节点的 BLOCK)都**不影响执行**——
    计划节点不参与准入裁决,也不被 act 读取(见下方「informational」边界说明)。
    """
    agent = getattr(ctx, "agent", None)
    think = getattr(agent, "_think", None)      # 轻量替身/测试 Agent 可能没有 _think
    if think is None:
        return
    try:
        think(content=_plan_text(ctx.plan), node_type="plan", conf=0.8,
              metadata=dict(ctx.plan))
    except Exception:
        pass


def stage_plan(ctx: PipelineContext) -> None:
    """结构规划:用 **P1-2 统一规划引擎** 分析任务结构(是否多步/并行),记录到 ctx.plan。

    **诚实边界(2026-07-08 审计校正)**:本 stage 纯 **informational**——写入 `ctx.plan` 供
    观测/审计,但**没有任何 `_execute` 读取 ctx.plan**,故当前不改变执行(act 整段交 LLM)。
    (2026-11-07 起额外落一条 `plan` 推理节点并经 SSE `{"plan":…}` 事件送前端:计划**可见**,
     但仍不驱动执行——「按 plan 逐步执行」是行为改变,仍需 opt-in + confirm 挂起,不在此做。)
    它与主管的实际分派**共用同一 P1-2 引擎**(engine 层已统一),但主管的分派在 SupervisorAgent
    自己的流程里、不经本管线。
    **它不做 skill 规划/dispatch**——skills 目前走独立治理面(`use_skill` / `ahs_pipeline`,
    经 `/api/ahs/*`),完全绕过本管线。"把 skill 接入管线的 act 阶段"是独立路线项
    (见 docs/internal/V2_ROADMAP_DESIGN.md「后续路线项 · Skill 接入认知管线」),属行为改变的特性,
    须 opt-in + 建模 confirm 挂起 + fail-safe,不在本 stage 顺手做。"""
    from ..planning import planner as P
    task = (ctx.task or "").strip()
    if any(k in task for k in P.PARALLEL_TRIGGERS):
        steps = P.split_on(task, P.PARALLEL_TRIGGERS + P.LIST_SEPS)
        flow = "parallel" if len(steps) > 1 else "single"
    else:
        steps = P.split_on(task, P.SPLIT_MARKERS)
        flow = "sequential" if len(steps) > 1 else "single"
    if len(steps) <= 1:
        steps, flow = [task], "single"
    ctx.plan = {"steps": steps, "flow": flow, "multi_step": len(steps) > 1}
    _record_plan_node(ctx)
stage_plan.stage_name = "plan"
# ⚠【预留·informational,不驱动执行】(2026-07-17 V3·B1 审核标注)
# stage_plan 写 ctx.plan 供观测/审计,但**没有任何 _execute 读取它**,故不改变执行
# (act 整段交 LLM/skill)。让 act 真按 plan 多步执行是**行为改变**,须 opt-in + 每步
# confirm 挂起 + fail-safe(见 V2_ROADMAP「Skill 接入认知管线」),不在此顺手做。
# (B1 自审:曾在此加 `stage_plan.informational=True` 属性,但无任何消费者=又一个死标记,
#  已删——注释足以说明,不造没人读的假结构化标记。)


# 默认自动派发的门槛(A·Step5,2026-07-16:从"默认关"改为"默认开但严格受限")。
# 背景:此前 route/dispatch 双双默认关 → 工具中心/MCP 连接器/技能台**在正常对话里根本够不着**
# (「帮我算 3+5」助手只会用 LLM 硬答,计算器技能不会被调用)。整套工具生态形同虚设。
# 放量策略:只自动派发**只读**技能、且要求**高置信**;有副作用的(write/send/pay/destructive)
# 一律不自动选中,仍须显式 use_skill —— 自动执行绝不越过"会改变世界"的那条线。
# 阈值取 0.6 —— 这是**量出来的**,不是拍的。router 实测分值只有三档:
#   0.600 强匹配(触发词直接命中,如「帮我计算…」→calculator)
#   0.480 弱匹配(如「查下去公司的路线」→amap_route、「发邮件给张三」→gmail_send)
#   0.000 无匹配(闲聊/写代码)
# 故 (0.48, 0.60] 区间内的任何值都等价于"只要强匹配";再高(如 0.75)则**一个都选不中**、
# 功能形同虚设。0.6 已是最保守的可用值 —— "比 0.6 更保守"是伪命题。
AUTO_ROUTE_THRESHOLD = 0.6
AUTO_ROUTE_READ_ONLY = True      # 只自动派发只读技能(有副作用的绝不自动动手)


def _is_read_only(skill) -> bool:
    """只读判定:显式 read_only 优先;否则按 side_effect 推断,且 destructive 一票否决。"""
    ro = getattr(skill, "read_only", None)
    if ro is not None:
        return bool(ro)
    return (getattr(skill, "side_effect", "write") == "read"
            and not getattr(skill, "destructive", False))


def _route_policy(agent, context: dict) -> dict:
    """决定 route 阶段是否选 skill、阈值、是否限只读。

    **默认开**(仅只读 + 高阈值),让已装好的技能/连接器在正常对话里真的可达。
    关闭途径:context['_route_skill']=False / Agent 级 ROUTE_SKILL_POLICY=False /
    环境变量 FOXCLAW_ROUTE_SKILL=0。显式给 dict 可覆盖阈值与只读限制(如
    {'threshold':0.5,'read_only':False})——那是调用方自己承担风险的显式选择。
    """
    p = context.get("_route_skill")
    if p is None:
        p = getattr(agent, "ROUTE_SKILL_POLICY", None)
    if p is None and os.environ.get("FOXCLAW_ROUTE_SKILL", "") == "0":
        p = False
    if p is False:
        return {"enabled": False, "threshold": AUTO_ROUTE_THRESHOLD, "read_only": True}
    if p is None or p is True:
        p = {}
    return {"enabled": True,
            "threshold": float(p.get("threshold", AUTO_ROUTE_THRESHOLD)),
            "read_only": bool(p.get("read_only", AUTO_ROUTE_READ_ONLY))}


def stage_route(ctx: PipelineContext) -> None:
    """路由决策:记录本任务由谁处理到 ctx.route(target=self 或 skill)。

    **A·Step1(2026-07-08,铺线,默认关、行为不变)**:开启后(见 `_route_policy`)用 P1-2 router
    在 Agent 的 `skill_registry` 上选主 skill,命中置信≥阈值 →
    `ctx.route={target:"skill", skill_id, confidence}`;否则/未开启/无 registry/选路异常 →
    `{target:"self"}`(与现状逐字一致,fail-safe 回落)。
    **本步仍不改变执行**:`stage_act` 尚未消费 `ctx.route`(真正按路由 dispatch skill 是 Step2),
    故即便选中 skill,act 仍走 `_execute`(LLM)。这里只把"路由到 skill 的决策"显式落到 ctx.route,
    为 Step2 的**受 PolicyGate 治理的 dispatch + confirm 挂起**铺线
    (见 docs/internal/V2_ROADMAP_DESIGN.md「Skill 接入认知管线」)。主管的子 Agent 分派仍在
    SupervisorAgent 流程里、不经本管线;`use_skill`/`ahs_pipeline` 独立面不受影响。"""
    ctx.route = {"target": "self", "reason": "领域 Agent 自处理"}
    policy = _route_policy(ctx.agent, ctx.context)
    if not policy["enabled"]:
        return
    reg = getattr(ctx.agent, "skill_registry", None)
    if reg is None:
        return                      # 无工具箱(finance/admin/office)→ 保持 self
    try:
        from ..skills.pipeline import route as skill_route
        r = skill_route(ctx.task, reg)
    except Exception:
        return                      # 选路失败不影响主流程 → 保持 self(fail-safe)
    if r.primary and r.confidence >= policy["threshold"]:
        sk = reg.get(r.primary)
        # 只读闸:有副作用的技能(write/send/pay/destructive)**绝不自动选中**——
        # 自动执行不得越过"会改变世界"的线;要用仍可显式 use_skill(那时用户是知情的)。
        if policy["read_only"] and sk is not None and not _is_read_only(sk):
            return                      # 保持 self → LLM 作答,不自动动手
        # 外泄闸:问的是**用户自己的数据**(账本/行程/学习)→ 绝不自动发给**外部**技能。
        # 与 needs_search 共用同一判定(单一真源)。否则「查一下最新的本月支出」会把私人
        # 财务问题自动送去外部搜索——那正是 needs_search 已挡住、却从技能这条旁路漏回来的 bug。
        if sk is not None and getattr(sk, "external", False):
            try:
                from ..core.web_search import is_personal_query
                if is_personal_query(ctx.task):
                    return              # 私人问题不外发 → 保持 self
            except Exception:
                pass                    # 判定不可用 → 不因此放行外部技能之外的行为(保持原逻辑)
        ctx.route = {"target": "skill", "skill_id": r.primary,
                     "confidence": round(float(r.confidence), 3),
                     "reason": f"router 选中 skill「{r.primary}」"
                               f"(置信 {r.confidence:.2f}≥{policy['threshold']})"}
stage_route.stage_name = "route"


def _skill_dispatch_enabled(agent, context: dict) -> bool:
    """act 阶段是否按 ctx.route 真正 dispatch skill。**默认开** —— 与 _route_policy 配套:
    route 只会选中「只读 + 高置信」的技能,选中了却不执行等于白选(工具依旧够不着)。
    真正的安全边界在 route 的只读闸 + SkillAdapter 的 PolicyGate,而非这个开关。

    关闭途径:context['_skill_dispatch']=False / Agent 级 PIPELINE_SKILL_DISPATCH=False /
    环境变量 FOXCLAW_PIPELINE_SKILL_DISPATCH=0。"""
    v = context.get("_skill_dispatch")
    if v is not None:
        return bool(v)
    v = getattr(agent, "PIPELINE_SKILL_DISPATCH", None)
    if v is not None:
        return bool(v)
    return os.environ.get("FOXCLAW_PIPELINE_SKILL_DISPATCH", "") != "0"


def _confirmed_for(context: dict, sid: str) -> bool:
    """本次 skill 是否已获确认。支持三种形态,**绑定优先**(防止确认漂移):
      · `_confirmed=True`  → 概括确认(向后兼容);
      · `_confirmed="<skill_id>"` / `{'skill_id': ...}` → **仅当匹配本次 sid 才确认**。
    深审加固:避免"用户确认了 A、但重跑时 route 选中 B 却被 _confirmed=True 概括放行",
    也避免会话级 _confirmed=True 概括自动执行非预期的有副作用 skill——确认应绑定到
    pending_action.skill_id(推荐调用方回传该 id 作 _confirmed)。"""
    c = context.get("_confirmed")
    if not c:
        return False
    if c is True:
        return True
    if isinstance(c, str):
        return c == sid
    if isinstance(c, dict):
        return c.get("skill_id") == sid
    return False


async def _act_via_skill(ctx: "PipelineContext"):
    """按 ctx.route 选中的 skill,经 **SkillAdapter(→PolicyGate 四阶段权限)** dispatch。
    返回 (result, extra),或 None 表示"无法走 skill,回落 _execute"。

    **安全铁律(A·Step2 的核心)**:
      · 经 adapter.invoke,权限门在 dispatch **之前**裁决——destructive/send/pay 类未确认 →
        needs_confirmation,**根本不执行、零副作用**(与直呼 skill 面同一治理)。
      · 非成功结果(needs_confirmation/blocked/circuit_open/...)**绝不静默当完成**,而是结构化
        标记 pending/status 回传;**绝不 fail-open**(呼应 P3)。把它提升为 run_task 顶层
        needs_confirmation 是 Step3。
      · 同步 invoke 放 to_thread,避免阻塞事件循环(与 _call_llm 一致)。"""
    agent = ctx.agent
    reg = getattr(agent, "skill_registry", None)
    adp = getattr(agent, "skill_adapter", None)
    if reg is None or adp is None:
        return None                       # 无工具箱(finance/admin/office)→ 回落 _execute
    sid = (ctx.route or {}).get("skill_id")
    skill = reg.get(sid) if sid else None
    if skill is None:
        return None
    from ..skills.pipeline import _build_args
    from ..skills import set_loop_session, reset_loop_session
    args = _build_args(skill, ctx.task, {})
    confirmed = _confirmed_for(ctx.context, sid)
    token = set_loop_session(getattr(ctx, "session_id", None))   # 循环检测按会话分槽
    try:
        res = await asyncio.to_thread(adp.invoke, skill, args, confirmed)
    except Exception as e:
        # invoke 理应自兜底(catches _dispatch 异常);万一权限评估等意外抛错 → **fail-closed**:
        # 结构化 error 回传,**绝不回落 LLM**(路由到发邮件却让 LLM 代答=幻觉/危险),也不崩整个任务。
        return ({"skill": sid, "status": "error", "message": f"skill dispatch 异常: {e}",
                 "pending": False}, {"via": "skill", "skill_id": sid, "skill_status": "error"})
    finally:
        reset_loop_session(token)
    extra = {"via": "skill", "skill_id": sid, "skill_status": res.status}
    if res.success:
        return ({"skill": sid, "output": res.output, "status": res.status}, extra)
    # 非成功:无副作用已发生(权限门+confirmed=False保证)。结构化回传,绝不当完成、绝不回落 LLM
    # (路由到发邮件却让 LLM 代答=危险/幻觉);needs_confirmation 待用户确认后重跑(带 _confirmed)。
    return ({"skill": sid, "status": res.status, "message": res.message,
             "pending": res.status == "needs_confirmation"}, extra)


async def stage_act(ctx: PipelineContext) -> None:
    """领域执行步骤。默认:领域 `_execute`(→ LLM),等价 P1-1 模板。

    **A·Step2(opt-in,首个真改执行的步骤)**:开启 skill dispatch(见 `_skill_dispatch_enabled`)
    且 `ctx.route.target=="skill"` 时,经 SkillAdapter(PolicyGate 治理)真正 dispatch 选中的 skill;
    无工具箱/无匹配 spec/未开启 → 回落 `_execute`(行为不变)。安全见 `_act_via_skill`。"""
    if (ctx.route or {}).get("target") == "skill" \
            and _skill_dispatch_enabled(ctx.agent, ctx.context):
        r = await _act_via_skill(ctx)
        if r is not None:
            ctx.result, ctx.extra = r
            return
    ctx.result, ctx.extra = await ctx.agent._execute(
        ctx.task, ctx.context, ctx.session_id, ctx.memories)
stage_act.stage_name = "act"


def stage_verify(ctx: PipelineContext) -> None:
    """把本次执行的 Harness 校验**结果**显式化到 ctx.verified(Step 2)。

    关键:Harness 校验本就**内联在 _think**(act 期间每个推理节点都过 harness.check),
    BLOCK 会抛 HarnessBlockedError 中断管线。故本 stage **不新增任何校验/拦截点**——
    只**读取**本 session 已产生的推理节点,汇总健康度(是否 WARN/最大漂移),使"校验结果"
    成为一等的管线值,供 Step 3 的 reflect 决策(低置信重试)。行为逐字不变。

    走到本 stage 即代表 act 未被 BLOCK(否则已在 act 抛异常中断)。"""
    a = ctx.agent
    try:
        nodes = a.thought_chain.get_chain(ctx.session_id, a.AGENT_ID)
    except Exception:
        nodes = []
    warns = sum(1 for n in nodes if getattr(n, "harness_status", "") == "WARN")
    deltas = [n.harness_delta for n in nodes            # get_chain 按 timestamp 升序
              if getattr(n, "harness_delta", None) is not None]
    ctx.verified = {
        "passed": True,                          # 未被 BLOCK
        "nodes": len(nodes),
        "warns": warns,
        "max_delta": round(max(deltas), 3) if deltas else 0.0,   # 全 session 峰值(信息量)
        # last_delta = 最近一次推理的漂移(本次尝试的最终状态)。reflect 用它作重试信号——
        # 深审修复:max_delta 是 max over 累积历史(单调不减),重试追加节点后仍是旧峰值,
        # 会让重试永远到上限、无法因"重试改善"提前退出;last_delta 反映当前尝试,可正确早退。
        "last_delta": round(deltas[-1], 3) if deltas else 0.0,
    }
stage_verify.stage_name = "verify"


def _reflect_policy(agent, context: dict) -> dict:
    """决定 reflect 是否启用重试及其上限。默认**关**(behavior-preserving):
    不显式开启时 reflect 只记录"accept"决策、绝不重跑。
    开启途径:context['_reflect']=True/{...} 或 Agent 级 REFLECT_POLICY={...}。"""
    p = context.get("_reflect")
    if p is None:
        p = getattr(agent, "REFLECT_POLICY", None)
    if not p:
        return {"enabled": False, "max_retries": 0, "drift_threshold": 1.1}
    if p is True:
        p = {}
    return {"enabled": True,
            "max_retries": int(p.get("max_retries", 1)),          # 硬上限,防成环
            "drift_threshold": float(p.get("drift_threshold", 0.5))}


async def stage_reflect(ctx: PipelineContext) -> None:
    """反思:读 verify 信号(max_delta),决定接受还是**有上限地**重试(Step 3)。

    默认接受 → 无行为变化(现有全部任务不受影响)。opt-in 开启后,当漂移超阈值且
    仍有重试额度时,重跑 act(领域执行)+ verify,直到达标或用尽额度。
    **fail-safe**:重试自身若出错(如重跑又漂移到 BLOCK),回滚到重试前的结果并停止——
    重试只会改善、绝不让结果变差(呼应 P3 的守门人 fail-safe 教训)。"""
    policy = _reflect_policy(ctx.agent, ctx.context)
    decision, retries = "accept", 0
    if policy["enabled"]:
        # 用 last_delta(当前尝试的最终漂移)作重试信号,而非 max_delta(累积峰值,单调不减)——
        # 否则重试改善了也无法早退。重试若把漂移降到阈值内,下一轮 last_delta 达标即停止。
        while (ctx.verified.get("last_delta", 0.0) > policy["drift_threshold"]
               and retries < policy["max_retries"]):
            # 协作式中断点(P3):每次重试都是一次 `_execute`(LLM 调用)——这里是本管线里最接近
            # 范式"agentic loop 每轮检查中断"的地方。置位则停在下一轮 LLM 调用**之前**(省 token),
            # 保留已有最好结果。opt-in:stop_event=None 恒不触发,重试行为逐字不变。
            if _stop_requested(ctx):
                # 中断:decision 统一记 "interrupted",已发生的重试次数由 retries 字段承载。
                # (B2 审核 F1:此前分 interrupted/retry_interrupted 两值,其中 retries==0 一支仅在
                #  外部线程于"pre-reflect 边界检查"与"首个 while 检查"之间置位才可达——单线程测试无法
                #  稳定覆盖。合并为单值消除该未覆盖分支,信息量不减: retries 已表达重试了几次。)
                ctx.interrupted = True
                decision = "interrupted"
                break
            prev_result, prev_extra = ctx.result, ctx.extra   # 重试前的最好结果
            try:
                retries += 1
                decision = "retry"
                ctx.result, ctx.extra = await ctx.agent._execute(
                    ctx.task, ctx.context, ctx.session_id, ctx.memories)
                stage_verify(ctx)                              # 重新汇总校验(刷新 last_delta)
            except Exception:
                # 重试出错(含重跑被 BLOCK)→ 回滚到上一次好结果,停止重试(不让重试变差)
                ctx.result, ctx.extra = prev_result, prev_extra
                decision = "retry_reverted"
                break
        # 重试后 last_delta 已达标 → 视为改善接受
        if decision == "retry" and ctx.verified.get("last_delta", 0.0) <= policy["drift_threshold"]:
            decision = "retry_accepted"
    ctx.reflect = {"decision": decision, "retries": retries,
                   "final_last_delta": ctx.verified.get("last_delta", 0.0),
                   "final_max_delta": ctx.verified.get("max_delta", 0.0)}
stage_reflect.stage_name = "reflect"


# 默认管线的 stage 序列(完整八阶段之七;persist=run_task 里的 emit + 领域 _store_memory)。
DEFAULT_STAGES = [stage_perceive, stage_recall, stage_plan, stage_route,
                  stage_act, stage_verify, stage_reflect]
