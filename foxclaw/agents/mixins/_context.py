"""Agent 会话状态基础设施:contextvar + 污点辅助 + Harness 截断异常。

从 `foxclaw/agents/base.py` 搬移出来的模块级符号,base.py 与各 mixin 均依赖它们。
"""
import contextvars

from ...core.thought_chain import ThoughtNode
from ...harness.harness import HarnessResult


# 每任务(per asyncio-Task / per-thread)会话状态。放在 contextvar 而非实例属性,使同一
# Agent 实例并发处理多个顶层任务时,各任务的会话指针互不覆盖(asyncio 每个 Task 自动持
# 独立 context 副本;旧实现下 await 后的推理节点会被最后启动的会话吞掉——已实测复现)。
# 写法:单个 module 级 contextvar 承载一个 dict、按"每实例 token"分槽:
#   ① GC 友好(只有一个 contextvar,不随实例增长);
#   ② 支持 supervisor→subagent 的同任务嵌套(不同实例 token 不冲突,互不覆盖);
#   ③ copy-on-write —— 一个任务的 set 生成新 dict,不影响其它任务。
_agent_ctx: contextvars.ContextVar = contextvars.ContextVar(
    "foxclaw_agent_ctx", default=None)

# 抑制记忆写入(随 to_thread/asyncio.run 上下文复制传播):MCP ask_foxclaw 驱动的外部任务
# 不写入 HMR,防外部客户端灌垃圾污染你的个人记忆。默认 False(正常任务照写)。
_suppress_memory: contextvars.ContextVar = contextvars.ContextVar(
    "foxclaw_suppress_memory", default=False)

# act 阶段 LLM 流式回调(D1,随 to_thread/asyncio.run 上下文复制传播):run_task_stream 设它,
# 承载 {"on_token": cb, "stop_event": ev};_call_llm 读到非 None 则走 router.stream 逐 token 回调
# + 累积返回完整串(返回值语义不变)。默认 None = 非流式(走 router.complete,行为逐字不变)。
_act_stream: contextvars.ContextVar = contextvars.ContextVar(
    "foxclaw_act_stream", default=None)

# 断点续跑(P1):{步骤指纹: {"name": 工具名, "result": 上次结果}}。run_task 带
# `context={"_resume_task_id": ...}` 时由 _run_task_core 从 recovery 日志装载,工具轮命中
# 同指纹步骤则复用结果、不再重复执行(避免重跑联网/副作用)。默认 None = 正常执行,行为不变。
_resume_steps: contextvars.ContextVar = contextvars.ContextVar(
    "foxclaw_resume_steps", default=None)

# E2 推理上下文注入(随 to_thread/asyncio.run 上下文复制传播):pre_reasoning(agent_start)钩子
# 返回的 additional_context 存这里,_call_llm 前插为一条 user 消息注入推理。默认 None = 不注入
# (无钩子/钩子未返回上下文时行为逐字不变)。
_reasoning_inject: contextvars.ContextVar = contextvars.ContextVar(
    "foxclaw_reasoning_inject", default=None)


# 执行证据(存在会话槽里,与污点同寿命):每次工具轮把「这一步有没有凭据」记下来,
# 最终由 stream_reply 一次性交给端点做 SSE `event: evidence`,前端展示给人核对。
# 放会话槽而非实例属性:同一 Agent 并发两个任务时证据不得串台。


def _evidence_reset(agent) -> None:
    tok = getattr(agent, "_ctx_token", None)
    if tok is not None:
        _agent_ctx_set(tok, evidence=[])
    else:
        agent._evidence_list = []


def _evidence_add(agent, record: dict) -> None:
    """追加一条步骤证据(fail-safe:证据记账失败绝不影响工具执行)。"""
    if not isinstance(record, dict):
        return
    try:
        tok = getattr(agent, "_ctx_token", None)
        if tok is not None:
            cur = _agent_ctx_get(tok).get("evidence") or []
            _agent_ctx_set(tok, evidence=list(cur) + [record])
        else:
            cur = getattr(agent, "_evidence_list", None) or []
            agent._evidence_list = list(cur) + [record]
    except Exception:
        pass


def _evidence_take(agent) -> list:
    """取走并清空(每次回复只上报一次,避免下一轮重复展示上一轮的证据)。"""
    tok = getattr(agent, "_ctx_token", None)
    if tok is not None:
        cur = list(_agent_ctx_get(tok).get("evidence") or [])
        _agent_ctx_set(tok, evidence=[])
        return cur
    cur = list(getattr(agent, "_evidence_list", None) or [])
    agent._evidence_list = []
    return cur


def _agent_ctx_get(token: str) -> dict:
    return (_agent_ctx.get() or {}).get(token) or {}


def _agent_ctx_set(token: str, **updates):
    d = dict(_agent_ctx.get() or {})
    slot = dict(d.get(token) or {})
    slot.update(updates)
    d[token] = slot
    _agent_ctx.set(d)


def _taint_get(agent) -> int:
    """读会话污点(已取回数据的最高敏感度)。真实 agent 走 contextvar 分槽(并发任务
    互不覆盖);fake agent(测试 SimpleNamespace)无 _ctx_token 时退化为实例属性或 0。"""
    tok = getattr(agent, "_ctx_token", None)
    if tok is not None:
        return _agent_ctx_get(tok).get("taint_level", 0)
    return getattr(agent, "_taint_level", 0)


def _taint_raise(agent, level: int) -> None:
    """把来源数据敏感度并入会话污点(取最大值,单调不减)。"""
    tok = getattr(agent, "_ctx_token", None)
    if tok is not None:
        _agent_ctx_set(tok, taint_level=max(_taint_get(agent), level))
    else:
        agent._taint_level = max(getattr(agent, "_taint_level", 0), level)


def _taint_reset(agent) -> None:
    """会话开始时清空污点(新任务从 TAINT_NONE 起算)。"""
    tok = getattr(agent, "_ctx_token", None)
    if tok is not None:
        _agent_ctx_set(tok, taint_level=0)
    else:
        agent._taint_level = 0


class HarnessBlockedError(Exception):
    """Harness 截断异常，携带完整的 HarnessResult"""
    def __init__(self, result: HarnessResult, node: ThoughtNode):
        self.result = result
        self.node = node
        super().__init__(f"Harness blocked: {result.reason}")
