"""
框架无关治理中间件的实现。

只依赖治理内核(Harness + ThoughtChain + ToolLoopDetector),不碰 Foxclaw 其余部分。
对外只暴露两个概念:
  · GovernanceSession —— 一次带目标基线的治理会话:observe(每步) + audit(取审计链)+ 循环检测;
  · governed          —— 把某个工具函数包成"调用前先过 Harness"的装饰器(同步/异步都支持)。
"""
from __future__ import annotations

import asyncio
import functools
import tempfile
import uuid
from dataclasses import dataclass, field
from typing import Optional

from ..harness.harness import Harness
from ..harness.loop_detection import ToolLoopDetector
from ..core.thought_chain import ThoughtChain, ThoughtNode


@dataclass
class GovResult:
    """一步治理判定的结果(框架无关,不泄露 Foxclaw 内部类型)。"""
    status: str                      # PASS / WARN / BLOCK
    blocked: bool
    warned: bool
    delta: float
    dimension: str                   # 主导漂移维度 semantic/tool/scope
    reason: Optional[str]
    node_id: str
    scores: dict = field(default_factory=dict)   # 各维度分 semantic/tool/scope
    loop: Optional[dict] = None      # 若检出工具死循环,填充告警

    @property
    def tool_violation(self) -> bool:
        """工具越权(与语言/语义无关的确定性信号)。"""
        return self.scores.get("tool", 0) >= 1.0

    @property
    def scope_violation(self) -> bool:
        """范围蔓延越上限(确定性信号)。"""
        return self.scores.get("scope", 0) >= 1.0

    def to_dict(self) -> dict:
        return {"status": self.status, "blocked": self.blocked, "warned": self.warned,
                "delta": round(self.delta, 4), "dimension": self.dimension,
                "reason": self.reason, "node_id": self.node_id,
                "scores": {k: round(v, 4) for k, v in self.scores.items()},
                "loop": self.loop}


class GovernanceBlocked(Exception):
    """被治理层拦截时抛出(供 governed 装饰器/框架回调使用)。"""
    def __init__(self, result: GovResult):
        self.result = result
        super().__init__(f"governance blocked: {result.reason}")


class GovernanceSession:
    """一次治理会话:注册目标基线,之后每步 observe 都过三维漂移 + 循环检测,并落审计链。

    参数:
      goal          —— 任务目标(语义漂移的基线);
      allowed_tools —— 允许的工具类别集合(如 {"read","finance"});越类别即工具越权;
      entities      —— 基线实体集(范围蔓延的参照);
      agent_id      —— 审计归属标识;
      shadow_rounds —— 观测期轮数(前 N 步只观测不拦截,用真实分布校准阈值);默认 0=即时判定;
      db_path       —— 审计链落盘路径;默认临时文件(会话级)。

    线程/进程无关:不启动任何后台线程,不联网;语义向量用本地缓存模型,缺失自动降级哈希嵌入。
    """

    def __init__(self, goal: str, allowed_tools=None, entities=None,
                 agent_id: str = "agent", shadow_rounds: int = 0,
                 db_path: str | None = None, policy_path: str | None = None,
                 embedding_model: str | None = None):
        self.agent_id = agent_id
        self._session_id = f"gov_{uuid.uuid4().hex[:10]}"
        self.thought_chain = ThoughtChain(
            db_path=db_path or tempfile.mktemp(suffix=".gov.db"))
        self.harness = Harness(
            agent_id=agent_id, thought_chain=self.thought_chain,
            policy_path=policy_path or tempfile.mktemp(suffix=".gov.yaml"),
            shadow_rounds=shadow_rounds, embedding_model=embedding_model)
        self.harness.register_goal(
            goal_text=goal,
            allowed_tool_categories=set(allowed_tools or {"read"}),
            baseline_entities=set(entities or []))
        # shadow_rounds<=0:中间件默认**即时按默认阈值判定**(warn 0.15 / block 0.25),
        # 不做"拿第一个样本自校准"(那会让首步逃逸并用单样本污染阈值)。想用真实分布
        # 校准的调用方,显式传 shadow_rounds>0 走观测期。
        if shadow_rounds <= 0:
            self.harness._shadow_mode = False
        self._loop = ToolLoopDetector()
        self._blocked_count = 0

    # ── 核心:观测一步 ────────────────────────────────────────
    def observe(self, content: str, tool: str | None = None, entities=None,
                node_type: str | None = None, conf: float = 0.9,
                semantic: bool = True) -> GovResult:
        """观测一个推理/工具步骤,返回治理判定。不抛异常(判定在结果里)。

        semantic=False:只按工具/范围维度判定(语义置 0)。用于纯"工具授权"检查——
        工具调用串对目标的语义距离是噪声。"""
        node = ThoughtNode.create(
            session_id=self._session_id, agent_id=self.agent_id,
            type=node_type or ("tool_intent" if tool else "reasoning"),
            content=content, conf=conf, tool_name=tool, entities=entities or [])
        self.thought_chain.add_node(node)
        r = self.harness.check(node, ignore_semantic=not semantic)
        loop_warn = None
        if tool:
            lv = self._loop.record(tool, {"content": content})
            if lv.stuck:
                loop_warn = lv.to_dict()
        if r.is_blocked:
            self._blocked_count += 1
        return GovResult(status=r.status, blocked=r.is_blocked, warned=r.is_warned,
                         delta=r.delta, dimension=r.dominant_dim, reason=r.reason,
                         node_id=node.id, scores=dict(r.scores), loop=loop_warn)

    def guard(self, content: str, tool: str | None = None, entities=None) -> GovResult:
        """与 observe 相同,但被 BLOCK 时**抛 GovernanceBlocked**(fail-closed 风格)。"""
        r = self.observe(content, tool=tool, entities=entities)
        if r.blocked:
            raise GovernanceBlocked(r)
        return r

    # ── 审计 ────────────────────────────────────────────────
    def audit(self) -> list[dict]:
        """取回本会话的完整可审计推理链(带每步 Harness 判定)。"""
        return [n.to_dict() for n in
                self.thought_chain.get_chain(self._session_id, self.agent_id)]

    def summary(self) -> dict:
        """治理摘要:总步数/拦截数/漂移趋势(供框架侧上报)。"""
        s = self.thought_chain.get_summary(self.agent_id)
        s["blocked_this_session"] = self._blocked_count
        s["shadow_mode"] = self.harness.is_shadow_mode
        return s

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def governed(session: GovernanceSession, tool_name: str | None = None, entities=None):
    """装饰器:把一个工具函数包成"调用前先过 Harness"。

    命中越权/跑题/范围蔓延 → 抛 GovernanceBlocked,函数不执行。同步/异步函数都支持。

        gov = GovernanceSession(goal=..., allowed_tools={"read"})
        @governed(gov)
        def delete_row(...): ...     # 调用即被拦(delete 不在 read 允许集)
    """
    def _check(name):
        # 工具装饰器只对"这个工具授权了吗"负责 → semantic=False 只按 tool/scope 判定
        # (语义是 observe() 的职责;工具名对目标的语义距离是噪声,跨语言时更会饱和,
        # 不能作为工具授权依据)。这样中间件语言无关、行为确定、审计不被语义噪声污染。
        r = session.observe(f"tool call: {name}", tool=name, entities=entities,
                            semantic=False)
        if r.blocked or r.tool_violation or r.scope_violation:
            raise GovernanceBlocked(r)

    def deco(fn):
        name = tool_name or getattr(fn, "__name__", "tool")

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrapper(*a, **kw):
                _check(name)
                return await fn(*a, **kw)
            return awrapper

        @functools.wraps(fn)
        def wrapper(*a, **kw):
            _check(name)
            return fn(*a, **kw)
        return wrapper
    return deco
