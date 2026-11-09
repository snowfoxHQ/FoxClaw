"""Agent 会话状态基础设施:contextvar + 污点辅助 + Harness 截断异常。

从 `foxclaw/agents/base.py` 搬移出来的模块级符号,base.py 与各 mixin 均依赖它们。
"""
import contextvars

from ...core.thought_chain import ThoughtNode
from ...harness.harness import HarnessResult


_agent_ctx: contextvars.ContextVar = contextvars.ContextVar(
    "foxclaw_agent_ctx", default=None)

_suppress_memory: contextvars.ContextVar = contextvars.ContextVar(
    "foxclaw_suppress_memory", default=False)

_act_stream: contextvars.ContextVar = contextvars.ContextVar(
    "foxclaw_act_stream", default=None)

_resume_steps: contextvars.ContextVar = contextvars.ContextVar(
    "foxclaw_resume_steps", default=None)

_reasoning_inject: contextvars.ContextVar = contextvars.ContextVar(
    "foxclaw_reasoning_inject", default=None)


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


def _untrusted_set(agent, flag: bool) -> None:
    """标本次任务「来源不可信」(外部聊天渠道 / 远程驱动)。与污点同槽同寿命。

    为什么单独一个位而不是直接抬污点:污点表达「取回过什么数据」,这个是「谁在下指令」,
    两者来源不同、生命周期也不同(任务开始就要定,不等任何召回)。
    """
    tok = getattr(agent, "_ctx_token", None)
    if tok is not None:
        _agent_ctx_set(tok, untrusted_source=bool(flag))
    else:
        agent._untrusted_source = bool(flag)


def _untrusted_get(agent) -> bool:
    """读「来源是否不可信」。fake agent(测试替身)无 _ctx_token 时退化为实例属性或 False。"""
    tok = getattr(agent, "_ctx_token", None)
    if tok is not None:
        return bool(_agent_ctx_get(tok).get("untrusted_source"))
    return bool(getattr(agent, "_untrusted_source", False))


class HarnessBlockedError(Exception):
    """Harness 截断异常，携带完整的 HarnessResult"""
    def __init__(self, result: HarnessResult, node: ThoughtNode):
        self.result = result
        self.node = node
        super().__init__(f"Harness blocked: {result.reason}")
