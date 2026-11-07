"""
LLM 驱动规划器（P0-1）—— 把主管的多步任务拆解从「规则拆分」升级为 LLM function-calling 规划。

病根（与 docs/internal/TOOL_USE_PLAN.md 同源）：planning/planner.py 的拆解靠
标点/连接词/触发词（"然后""同时""接着"），只能做表面切分，无法理解任务的语义结构；
LLM 从没机会决定「这个任务该拆成几步、每步派给哪个 Agent」。于是「帮我查最近一周支出，
再据此定下月预算，最后整理成邮件」这类真实任务，规则拆解经常切错边界、派错 Agent。

本模块给出单一真源：把「规划」声明成一个 function-calling 工具（submit_plan），交给
LLM 自主拆解，返回结构化计划（每步带目标 Agent 与依赖），由 SupervisorAgent 转成
Plan 兼容对象。

后半段「反思重规划」：多步执行出现失败/未完成时，再调一次 submit_plan，把上一轮
各步结果作为反馈喂给 LLM，让它决定「换 Agent / 换表述 / 拆更细 / 补前置 / 放弃」，
产出修正计划并重新执行（llm_replan）。这就是 plan → 执行 → 反思 → 重规划的闭环。

opt-in + fail-safe（与 FOXCLAW_TOOL_USE 同哲学，默认关、可逆）：
  · FOXCLAW_LLM_PLAN=1 才开启初次规划；
  · FOXCLAW_LLM_REPLAN=1 才开启反思重规划；
  · provider 不支持 tools / 调用失败 / 模型未产出有效计划 → 返回 None，
    上层自动回退（规划回退规则拆解；重规划回退「保留原结果」）。
"""
from __future__ import annotations

import os


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "0").strip() in ("1", "true", "True", "yes", "on")


def llm_plan_enabled() -> bool:
    """是否启用 LLM 规划。默认关（behavior-preserving / 可逆）。"""
    return _env_flag("FOXCLAW_LLM_PLAN")


def replan_enabled() -> bool:
    """是否启用反思重规划。默认关（behavior-preserving / 可逆）。"""
    return _env_flag("FOXCLAW_LLM_REPLAN")


_PLAN_SYSTEM = (
    "你是 Foxclaw 的任务规划器。唯一职责：把用户任务拆解成可执行计划，"
    "并为每个子任务指派最合适的 Agent。\n"
    "规则：\n"
    "1. 只拆解真正需要多步或跨领域协作的任务；简单单步任务就返回单个 step。\n"
    "2. 每个子任务独立可执行、边界清晰、动词开头、不含模糊表述。\n"
    "3. 子任务有先后依赖（后一步要用前一步的结果）→ flow=sequential；"
    "彼此独立可同时做 → flow=parallel。\n"
    "4. 必须通过 submit_plan 工具提交计划，不要输出计划之外的任何文本。\n\n"
    "可派发的 Agent 及其职责：\n{agent_desc}"
)

_REPLAN_SYSTEM = (
    "你是 Foxclaw 的反思规划器。上一轮多步计划的执行结果已经给出，其中存在失败或未完成的步骤。"
    "你的唯一职责：分析失败原因，产出修正后的计划，让任务最终完成。\n"
    "规则：\n"
    "1. 只重排**失败/未完成**的步骤，已经成功的步骤不要重复执行。\n"
    "2. 可采取的修正手段：换 Agent、换表述、拆得更细、补前置步骤、合并步骤。\n"
    "3. 若确认任务无法完成（超出能力 / 信息不足 / 反复失败），返回空 steps 数组表示放弃。\n"
    "4. 必须通过 submit_plan 工具提交计划，不要输出计划之外的任何文本。\n\n"
    "可派发的 Agent 及其职责：\n{agent_desc}"
)


def build_plan_tool(agent_catalog: dict) -> dict:
    """构造 submit_plan 的 function schema（OpenAI tools 通用格式）。

    agent_catalog: {agent_id: 职责描述}，用于约束 agent 字段的取值枚举，
    让 LLM 只能在真实存在的 Agent 里选，避免幻觉出不存在的能力。

    工具描述保持中性（planning / replanning 共用同一工具），具体行为由
    system prompt（_PLAN_SYSTEM 或 _REPLAN_SYSTEM）引导。
    """
    agent_ids = list(agent_catalog.keys())
    return {
        "type": "function",
        "function": {
            "name": "submit_plan",
            "description": (
                "提交任务执行计划（planning/replanning 共用）。steps 为子任务列表，"
                "每步含 name(一句话、动词开头) / agent(负责的 agent id) / depends_on"
                "(依赖的前序步骤序号,0-based)。flow 为执行模式。若无需拆解或确认"
                "无法完成，返回空 steps。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "flow": {
                        "type": "string",
                        "enum": ["single", "sequential", "parallel"],
                        "description": "single=单步；sequential=步骤有先后依赖；"
                                       "parallel=步骤彼此独立可并行",
                    },
                    "steps": {
                        "type": "array",
                        "description": "子任务列表（flow=single 时仅 1 项）",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "子任务的一句话描述，动词开头",
                                },
                                "agent": {
                                    "type": "string",
                                    "enum": agent_ids,
                                    "description": "负责该子任务的 agent id",
                                },
                                "depends_on": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "description": "依赖的前序步骤序号（0-based）；"
                                                   "无依赖传空数组",
                                },
                            },
                            "required": ["name", "agent"],
                        },
                    },
                },
                "required": ["flow", "steps"],
            },
        },
    }


def _agent_desc(agent_catalog: dict) -> str:
    return "\n".join(f"- {a}：{d}" for a, d in agent_catalog.items())


def _invoke_plan_tool(router, system: str, user_content: str, agent_catalog: dict,
                      max_tokens: int):
    """调用 submit_plan 工具并解析为 {intent, flow, steps}。

    返回 None = 不可用 / 不支持 tools / 调用失败 / 参数解析失败。
    成功时 steps 可能为空列表（模型判定「无需拆解」或「放弃」），由上层决定含义。
    """
    if not agent_catalog or not user_content or not str(user_content).strip():
        return None
    try:
        tool = build_plan_tool(agent_catalog)
        res = router.call_tools(
            system,
            [{"role": "user", "content": str(user_content).strip()}],
            [tool],
            tool_choice="auto",
            max_tokens=max_tokens,
        )
    except Exception:
        return None
    if not res.get("supported"):
        return None
    calls = res.get("tool_calls") or []
    if not calls:
        return None                      # 模型未调用工具 → 不可用，回退
    if calls[0].get("args_parse_failed"):
        return None                      # 参数 JSON 解析失败 → 不可信，回退
    args = calls[0].get("args") or {}
    steps = args.get("steps")
    if not isinstance(steps, list):
        return None
    norm = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip()
        if not name:
            continue
        agent = str(s.get("agent") or "office").strip()
        if agent not in agent_catalog:
            agent = "office"             # 幻觉出不存在的 Agent(含已下放的 assistant/admin)→ 兜底办公学习
        dep = s.get("depends_on") or []
        if not isinstance(dep, list):
            dep = []
        norm.append({
            "name": name,
            "agent": agent,
            "depends_on": [int(d) for d in dep if isinstance(d, (int, float))],
        })
    flow = str(args.get("flow") or "sequential").strip()
    if flow not in ("single", "sequential", "parallel"):
        flow = "sequential"
    if len(norm) == 1:
        flow = "single"
    return {"intent": str(user_content).strip(), "flow": flow, "steps": norm}


def llm_plan(router, task: str, agent_catalog: dict, max_tokens: int = 1024):
    """初次规划：用 function-calling 让 LLM 产出结构化计划。

    返回 {intent, flow, steps: [{name, agent, depends_on}]}；
    返回 None = 不可用 / 不支持 tools / 调用失败 / 模型未产出有效计划（上层回退规则拆解）。
    """
    out = _invoke_plan_tool(
        router,
        _PLAN_SYSTEM.format(agent_desc=_agent_desc(agent_catalog)),
        task, agent_catalog, max_tokens)
    if not out or not out["steps"]:
        return None
    return out


def _format_prior_results(prior_results) -> str:
    """把上一轮各步结果序列化为 LLM 可读的反馈文本。"""
    if not prior_results:
        return "(无执行结果)"
    lines = []
    for i, s in enumerate(prior_results, 1):
        status = "失败" if (s.get("blocked") or s.get("failed")) else "成功"
        lines.append(
            f"第{i}步[{s.get('agent', '?')}]（{status}）：{s.get('subtask', '')}\n"
            f"  输出：{s.get('output', '')}")
    return "\n".join(lines)


def llm_replan(router, task: str, prior_results, agent_catalog: dict,
               max_tokens: int = 1024):
    """反思重规划：把上一轮执行结果作为反馈，让 LLM 产出修正计划。

    prior_results: 上一轮各步结果列表，形如 [{subtask, agent, output, blocked, failed}, ...]
    返回同 llm_plan 的结构；steps 为空 = 模型判定「放弃/无需重试」；
    返回 None = 不可用 / 不支持 / 失败 / 解析失败（上层保留原结果）。
    """
    feedback = _format_prior_results(prior_results)
    user_content = (
        f"原始任务：{task}\n\n"
        f"上一轮执行结果：\n{feedback}\n\n"
        f"请反思失败原因，产出修正后的计划（若确认无法完成则返回空 steps）。")
    return _invoke_plan_tool(
        router,
        _REPLAN_SYSTEM.format(agent_desc=_agent_desc(agent_catalog)),
        user_content, agent_catalog, max_tokens)
