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

from typing import Literal, cast


class Hooks:
    """标准钩子点常量。用 Hooks.BEFORE_TOOL_CALL 而非裸字符串,避免拼写错误。"""

    SESSION_START = "session_start"
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    SESSION_END = "session_end"

    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    ON_ERROR = "on_error"
    ON_LOOP = "on_loop"

    BEFORE_MESSAGE_WRITE = "before_message_write"


ALL_HOOKS = frozenset({
    Hooks.SESSION_START, Hooks.AGENT_START, Hooks.AGENT_END, Hooks.SESSION_END,
    Hooks.BEFORE_TOOL_CALL, Hooks.AFTER_TOOL_CALL, Hooks.ON_ERROR, Hooks.ON_LOOP,
    Hooks.BEFORE_MESSAGE_WRITE,
})

BLOCKING_HOOKS = frozenset({Hooks.BEFORE_TOOL_CALL})

CONTEXT_HOOKS = frozenset({Hooks.AGENT_START})

TOOL_SCOPED_HOOKS = frozenset({
    Hooks.BEFORE_TOOL_CALL, Hooks.AFTER_TOOL_CALL, Hooks.ON_ERROR, Hooks.ON_LOOP,
})

ALIAS = {
    "pre_skill": Hooks.BEFORE_TOOL_CALL,
    "post_skill": Hooks.AFTER_TOOL_CALL,
    "pre_reasoning": Hooks.AGENT_START,
    "task_complete": Hooks.AGENT_END,
}


HookEvent = Literal["before_tool_call", "after_tool_call", "on_error", "on_loop",
                    "session_start", "agent_start", "agent_end", "session_end",
                    "before_message_write",
                    "pre_skill", "post_skill", "pre_reasoning", "task_complete"]


def normalize_hook(event: str) -> HookEvent:
    """把旧钩子名规整为标准名;已是标准名则原样返回。"""
    return cast(HookEvent, ALIAS.get(event, event))


def is_valid_hook(event: str) -> bool:
    return normalize_hook(event) in ALL_HOOKS
