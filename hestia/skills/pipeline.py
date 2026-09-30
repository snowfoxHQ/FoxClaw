"""
AHS 执行链 (Execution Pipeline) —— Agent Harness Skills 标准的全面落地。

把 6 个元 skill 串成一条可运行的执行链:

  task → [router 选主skill] → 复杂? → [planner 拆解]
                                    → [composition 编排多skill]
       每步 skill 执行都过 [guard 权限] + [breaker 断路] + [hooks 钩子]
       全程 IO 遵循 [execution-contract],结果可审计

router / planner / composition 在这里实现;guard / breaker / hooks 复用
permission.py + lifecycle.py;contract 复用 SkillResult。
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional

from ..planning import planner as P   # 统一规划原语(P1-2:否定/拆分/打分单一真源)


# ============ skill-router:委托统一引擎,候选空间=skill ============

@dataclass
class RouteResult:
    primary: Optional[str] = None        # 选中的主 skill id
    confidence: float = 0.0
    candidates: list = field(default_factory=list)   # [(skill_id, score)]
    fallback: Optional[str] = None
    needs_composition: bool = False
    reason: str = ""


def route(task: str, registry, fallback_id: str = None) -> RouteResult:
    """skill-router:把 skill 适配成通用候选,交统一引擎 trigger_route 打分选择。
    算法(否定过滤触发命中 0.8 + 描述领域匹配 0.2)现由 hestia.planning 单一真源提供。"""
    skills = registry.all()
    if not skills:
        return RouteResult(reason="注册表为空")
    cands = [P.ScoredCandidate(
                id=s.id,
                triggers=getattr(s, "triggers", []) or [],
                description=getattr(s, "description", "") or "")
             for s in skills]
    r = P.trigger_route(task, cands, fallback_id=fallback_id)
    return RouteResult(primary=r.primary, confidence=r.confidence,
                       candidates=r.candidates, fallback=r.fallback,
                       needs_composition=r.needs_composition, reason=r.reason)


# ============ planner:任务拆解 ============

@dataclass
class PlanTask:
    id: str
    name: str
    suggested_skill: Optional[str] = None
    dependencies: list = field(default_factory=list)


@dataclass
class Plan:
    intent: str
    tasks: list = field(default_factory=list)        # [PlanTask]
    flow_type: str = "sequential"                    # sequential/parallel/hybrid
    steps: list = field(default_factory=list)        # 执行顺序 [task_id]
    needs_input: bool = False
    missing: list = field(default_factory=list)


def plan(task: str, registry) -> Plan:
    """planner:把任务拆成结构化计划。拆分/并行标记与算法由 hestia.planning 单一真源提供。

    - 含并行连接词(同时/分别/并行)→ 拆成**无依赖**子任务,flow_type=parallel;
    - 含顺序连接词(然后/接着…)→ 拆成**线性依赖链**,flow_type=sequential;
    - 都没有 → 单步。
    依赖关系决定能否并行:parallel 的子任务 dependencies 为空,sequential 的链式依赖前一步。
    """
    task = (task or "").strip()
    if not task:
        return Plan(intent="", needs_input=True, missing=["task"])

    if any(k in task for k in P.PARALLEL_TRIGGERS):
        parts = P.split_on(task, P.PARALLEL_TRIGGERS + P.LIST_SEPS)
        flow = "parallel" if len(parts) > 1 else "sequential"
    else:
        parts = P.split_on(task, P.SPLIT_MARKERS)
        flow = "sequential"
    if len(parts) <= 1:
        parts, flow = [task], "sequential"

    tasks, steps = [], []
    for i, p in enumerate(parts, 1):
        r = route(p, registry)
        tid = f"T{i}"
        # 顺序:依赖前一步;并行:无依赖(可同时 fork)
        deps = [f"T{i-1}"] if (i > 1 and flow == "sequential") else []
        tasks.append(PlanTask(id=tid, name=p, suggested_skill=r.primary,
                              dependencies=deps))
        steps.append(tid)

    return Plan(intent=task, tasks=tasks, flow_type=flow, steps=steps)


# ============ composition:多 skill 编排执行 ============

@dataclass
class StageResult:
    stage: int
    skill_id: str
    result: dict


@dataclass
class CompositionResult:
    mode: str
    stages: list = field(default_factory=list)       # [StageResult]
    final_output: dict = field(default_factory=dict)
    failed: list = field(default_factory=list)
    rolled_back: bool = False
    rollback_clean: bool = True                       # 回滚是否干净完成(无补偿失败/无需人工)
    rollback_log: list = field(default_factory=list)  # 回滚记录 [{skill,status,detail}]
    needs_manual: list = field(default_factory=list)  # 需人工处理的 skill(无补偿/补偿失败)


def _build_args(skill, task_text: str, upstream: dict) -> dict:
    """把任务文本映射到 skill 声明的参数。

    简化策略:单参数 skill → 把任务文本(或提取的算式)塞给那个参数;
    多参数 skill → 交给 skill 自己解析(传 task + upstream)。
    真实系统里这一步由 LLM 做参数抽取;此处做轻量映射保证可运行。
    """
    params = skill.params or {}
    if not params:
        return {}
    # 计算器类:从文本提取数学表达式
    if "expression" in params:
        m = re.search(r"[-+*/().\d\s]+", task_text)
        expr = (m.group(0).strip() if m else task_text)
        return {"expression": expr or task_text}
    # 单参数 skill:把整段任务文本作为该参数
    if len(params) == 1:
        only = next(iter(params))
        return {only: task_text}
    # 多参数:传 task,让下游(或 LLM)解析;附带上游
    return {"task": task_text, "upstream_outputs": dict(upstream)}


def _rollback(executed, registry, res: CompositionResult):
    """逆序回滚已执行且有副作用的步骤。

    executed: [(skill_id, output)] 按执行顺序。
    只回滚有副作用(非只读)的;有 compensate 的调用补偿,无的标记需人工处理。
    """
    res.rolled_back = True
    for sid, output in reversed(executed):
        skill = registry.get(sid)
        if skill is None:
            continue
        side_effect = getattr(skill, "side_effect", "read")
        if side_effect == "read":
            continue   # 只读操作无副作用,无需回滚
        comp = getattr(skill, "compensate", None)
        if comp is not None:
            try:
                comp(output)
                res.rollback_log.append({"skill": sid, "status": "compensated",
                                         "detail": "已调用补偿动作撤销副作用"})
            except Exception as e:
                res.rollback_log.append({"skill": sid, "status": "compensate_failed",
                                         "detail": f"补偿失败: {e}"})
                res.rollback_clean = False          # 补偿失败 → 回滚不干净
                res.needs_manual.append(sid)
        else:
            res.rollback_log.append({"skill": sid, "status": "manual_required",
                                     "detail": f"{side_effect} 操作无补偿动作,需人工处理"})
            res.rollback_clean = False              # 有副作用却无法补偿 → 不干净
            res.needs_manual.append(sid)


def compose(plan_obj: Plan, registry, adapter, confirmed: bool = False) -> CompositionResult:
    """skill-composition:按计划顺序执行各 skill;关键路径失败 → 逆序回滚补偿。"""
    res = CompositionResult(mode=plan_obj.flow_type)
    upstream = {}     # skill_id -> output(供下游读取)
    executed = []     # [(skill_id, output)] 按执行顺序,供回滚
    for i, t in enumerate(plan_obj.tasks, 1):
        sid = t.suggested_skill
        if not sid or not registry.get(sid):
            res.failed.append(t.id); continue
        skill = registry.get(sid)
        call_args = _build_args(skill, t.name, upstream)
        out = adapter.invoke(skill, call_args, confirmed=confirmed)
        res.stages.append(StageResult(stage=i, skill_id=sid, result=out.to_dict()))
        if out.success:
            upstream[sid] = out.output
            executed.append((sid, out.output))
        else:
            res.failed.append(t.id)
            res.final_output = {"error": out.message, "failed_at": t.id}
            # 关键路径失败 → 逆序回滚已执行的有副作用步骤
            _rollback(executed, registry, res)
            return res
    res.final_output = upstream
    return res


# ============ 编排器:把 6 个元 skill 串起来 ============

@dataclass
class PipelineResult:
    task: str
    route: dict
    plan: dict
    composition: dict
    success: bool
    summary: str


class AHSPipeline:
    """AHS 执行链编排器:一条龙跑通 router→planner→composition,全程过 guard+breaker+hooks。"""

    def __init__(self, registry, adapter):
        self.registry = registry
        self.adapter = adapter

    def run(self, task: str, confirmed: bool = False) -> PipelineResult:
        # session_start 钩子
        self.adapter.hooks.fire("session_start", "", {"task": task})

        # 1. router 选主 skill
        r = route(task, self.registry)

        # 2. planner 拆解(总是出计划;简单任务=单步)
        p = plan(task, self.registry)

        # 3. composition 编排执行(每步内部过 guard+breaker+hooks)
        c = compose(p, self.registry, self.adapter, confirmed=confirmed)

        # session_end 钩子
        self.adapter.hooks.fire("session_end", "", {"task": task})

        success = not c.failed and bool(c.stages)
        summary = (f"执行 {len(c.stages)} 步,"
                   f"{'全部成功' if success else '部分失败:' + ','.join(c.failed)}")
        return PipelineResult(
            task=task,
            route={"primary": r.primary, "confidence": r.confidence,
                   "needs_composition": r.needs_composition},
            plan={"intent": p.intent, "steps": p.steps,
                  "tasks": [{"id": t.id, "name": t.name, "skill": t.suggested_skill}
                            for t in p.tasks]},
            composition={"mode": c.mode, "stages": len(c.stages),
                         "failed": c.failed, "final": c.final_output,
                         "rolled_back": c.rolled_back, "rollback_clean": c.rollback_clean,
                         "rollback_log": c.rollback_log, "needs_manual": c.needs_manual},
            success=success, summary=summary)
