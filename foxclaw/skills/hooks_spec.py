"""
Foxclaw — 标准生命周期钩子点 (Lifecycle Hooks Specification)

借鉴 OpenClaw 的标准钩子命名体系,把原本零散的钩子点规整成清晰的生命周期。
钩子分两类:
  · 会话级:贯穿一次 Agent 任务(session_start → agent_start → ... → agent_end → session_end)
  · 工具级:每次 skill 调用(before_tool_call → after_tool_call / on_error / on_loop)

阻断权限:只有 before_tool_call(原 pre_skill)被授予阻断权——它是守门人。
其余钩子是观察者(顾问不是控制器),失败不影响主链路。

向后兼容:旧名(pre_skill/post_skill)仍可用,通过 ALIAS 映射到标准名。
"""
from __future__ import annotations


# ── 标准钩子点(生命周期顺序)──────────────────────────────
class Hooks:
    """标准钩子点常量。用 Hooks.BEFORE_TOOL_CALL 而非裸字符串,避免拼写错误。"""

    # 会话级
    SESSION_START = "session_start"     # 一次任务开始
    AGENT_START = "agent_start"         # Agent 开始处理(分诊后)
    AGENT_END = "agent_end"             # Agent 处理完成
    SESSION_END = "session_end"         # 一次任务结束

    # 工具/skill 级
    BEFORE_TOOL_CALL = "before_tool_call"   # 调用前(可阻断/可改参数)
    AFTER_TOOL_CALL = "after_tool_call"     # 调用成功后
    ON_ERROR = "on_error"                   # 调用失败
    ON_LOOP = "on_loop"                     # 检测到死循环

    # 消息级
    BEFORE_MESSAGE_WRITE = "before_message_write"   # 写入/发送消息前(可改写)


# 所有标准钩子点(供校验)
ALL_HOOKS = frozenset({
    Hooks.SESSION_START, Hooks.AGENT_START, Hooks.AGENT_END, Hooks.SESSION_END,
    Hooks.BEFORE_TOOL_CALL, Hooks.AFTER_TOOL_CALL, Hooks.ON_ERROR, Hooks.ON_LOOP,
    Hooks.BEFORE_MESSAGE_WRITE,
})

# 拥有阻断权的钩子(只有这些返回 {block:True} 才生效)
BLOCKING_HOOKS = frozenset({Hooks.BEFORE_TOOL_CALL})

# 可注入 additionalContext 的钩子(E2:非阻断,但其返回的 additional_context 会被并入推理上下文)。
# agent_start = 一次 Agent 任务的推理开始点,对应范式 pre_reasoning —— 推理前注入上下文的落点。
CONTEXT_HOOKS = frozenset({Hooks.AGENT_START})

# 工具级钩子(按 skill_id 做 matcher 过滤)
TOOL_SCOPED_HOOKS = frozenset({
    Hooks.BEFORE_TOOL_CALL, Hooks.AFTER_TOOL_CALL, Hooks.ON_ERROR, Hooks.ON_LOOP,
})

# 向后兼容 + 语义别名 → 标准名
ALIAS = {
    "pre_skill": Hooks.BEFORE_TOOL_CALL,
    "post_skill": Hooks.AFTER_TOOL_CALL,
    # E2:推理层/会话层语义名(对齐范式 pre_reasoning / Stop),规整到已有的会话级钩子点——
    # 顺带**复活**此前定义却从未被 fire 的 agent_start / agent_end 孤儿钩子。
    "pre_reasoning": Hooks.AGENT_START,   # 一次 Agent 任务推理开始前(可注入 additionalContext)
    "task_complete": Hooks.AGENT_END,     # 一次 Agent 任务结束后(只读观测/上报,对应范式 Stop)
}


def normalize_hook(event: str) -> str:
    """把旧钩子名规整为标准名;已是标准名则原样返回。"""
    return ALIAS.get(event, event)


def is_valid_hook(event: str) -> bool:
    return normalize_hook(event) in ALL_HOOKS
