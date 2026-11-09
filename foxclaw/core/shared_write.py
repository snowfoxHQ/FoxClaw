"""
共享层写入策略 (SharedWritePolicy) —— P0-3

问题
────
memory_adapter.ingest(..., shared=True) 此前**没有任何权限校验**。而共享层是所有 Agent
都能召回的记忆区 —— 于是「谁能往共享层写什么」等价于「谁能给所有 Agent 留话」。

一个具体危害:办公 Agent 可以写一条「财务应给办公转账」进共享层,之后财务 Agent 在召回
时读到它。这是一条**完全绕开守门人**的跨 Agent 注入通道 —— 守门人管的是"动作",而共享层
写的是"下一轮推理的上下文"。

本模块只做一件事:把「谁写了什么到共享层」变成**可判定、可观测**的事实。

三种模式(FOXCLAW_SHARED_WRITE_MODE)
──────────────────────────────────
  audit  (默认)  只记录,不拦截。写入照常落库,事件里标 allowed=True/False。
  warn           记录 + 进程内告警,仍不拦截。
  enforce        真正拦截:未被策略允许的写入抛 SharedWriteDenied。

为什么默认 audit
────────────────
现有代码有 8 处**合法**共享写入(主管分发记录 6 处、各 Agent 的用户偏好共享 1 处、
真值管线沉淀 1 处)。策略表在真实流量上跑通之前是一份**待验证的假设**,不是既成事实 ——
贸然 enforce 会误伤正常链路。先在 audit 下看清越界写入长什么样,再切 enforce。
(这也是文稿 P0-3 的建议:三模式、默认灰度。)

范围说明(诚实标注)
──────────────────
本模块**不改变召回侧的隔离**。召回仍由 memory_adapter 的 tag 过滤实现(`agent:<id>` +
`shared`),那是"标签约定"而非结构性隔离 —— 属另一议题,不在本次范围。
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass
from typing import Iterable, Optional

MODE_ENV = "FOXCLAW_SHARED_WRITE_MODE"
VALID_MODES = ("audit", "warn", "enforce")

# 与写入方无关、任何 Agent 都可写的 facet(设计上就该跨 Agent 共享的东西):
#   preference     —— 用户偏好:base.py 明确以 shared=True 写入,意在跨会话跨 Agent 生效
#   truth_pipeline —— 真值管线沉淀(truth_pipeline.py),写入方是 agent_id="system"
GLOBAL_ALLOWED_FACETS = frozenset({"preference", "truth_pipeline"})

# 各 Agent 的「职责域」允许 facet。facet 取自一次写入的 {type:<记忆类型>} ∪ tags,
# 支持 fnmatch 通配(如 finance.* 可匹配 finance.budget)。
#
# 注意:这是**起始假设**,不是已验证的结论 —— 默认 audit 模式的目的正是用真实流量验证它。
DEFAULT_SHARED_WRITE_ALLOWED = {
    "supervisor": {"*"},      # 主管:分发/协调记录,语义上允许任意
    "system":     {"*"},      # 系统(真值管线、后台任务)
    "finance":    {"finance.*"},
    # 行政并入办公后,办公学习 Agent 同时拥有学习与行程两个写域;
    # 助手已下放为技能(不再有独立 agent_id),不再单列 ACL 条目。
    "office":     {"learning.*", "itinerary.*", "schedule.*"},
}


class SharedWriteDenied(PermissionError):
    """共享层写入被策略拒绝(enforce 模式下抛出)。"""

    def __init__(self, agent_id: str, reason: str, content: str = ""):
        self.agent_id = agent_id
        self.reason = reason
        self.content = content
        super().__init__(f"共享层写入被拒 agent={agent_id!r}: {reason}")


@dataclass
class Decision:
    """一次共享层写入的判定结果。"""
    allowed: bool
    reason: str
    mode: str
    facets: tuple = ()

    def to_dict(self) -> dict:
        return {"allowed": self.allowed, "reason": self.reason,
                "mode": self.mode, "facets": list(self.facets)}


class SharedWritePolicy:
    """判定「某 Agent 能否把某条内容写入共享层」。

    无状态、无锁、纯函数式判定 —— 并发安全直接成立,也便于单独测试。
    """

    def __init__(self, allowed: dict | None = None, mode: str | None = None,
                 global_facets: Iterable[str] | None = None):
        self.allowed = {k: set(v) for k, v in
                        (allowed or DEFAULT_SHARED_WRITE_ALLOWED).items()}
        self.global_facets = set(global_facets if global_facets is not None
                                 else GLOBAL_ALLOWED_FACETS)
        self.mode = self._resolve_mode(mode)

    @staticmethod
    def _resolve_mode(mode: str | None = None) -> str:
        """显式入参 > 环境变量 > 默认 audit。非法值一律回落到 audit(保守:不拦截)。"""
        m = (mode or os.environ.get(MODE_ENV, "") or "audit").strip().lower()
        return m if m in VALID_MODES else "audit"

    # 纯标记类 tag:描述"这条记忆的归属/去向",不描述"内容是什么"。
    # 让它们参与内容域匹配只会稀释策略表 —— 尤其 "shared" 在本闸门语境下恒为真,零信息量。
    _MARKER_TAGS = ("agent:", "shared")

    @staticmethod
    def facets_of(memory_type: str, tags: Iterable[str]) -> tuple:
        """一次写入的 facet 集合:{type:<记忆类型>} ∪ 原样 tag(去重保序)。

        `agent:<id>` 与 `shared` 被刻意排除 —— 它们是**隔离/去向标记**,描述"谁的、
        写给谁看的",不是"什么内容",拿它们参与内容域匹配只会让策略表变得没有意义。
        """
        out = []
        if memory_type:
            out.append(f"type:{memory_type}")
        for t in (tags or ()):
            if not isinstance(t, str) or not t:
                continue
            if t == "shared" or t.startswith("agent:"):
                continue
            out.append(t)
        return tuple(dict.fromkeys(out))

    def evaluate(self, agent_id: str | None = None, memory_type: str = "concept",
                 tags: Iterable[str] | None = None) -> Decision:
        facets = self.facets_of(memory_type, tags or ())
        who = agent_id or "unknown"

        if any(fnmatch.fnmatchcase(f, p) for f in facets for p in self.global_facets):
            return Decision(True, "命中全局允许 facet", self.mode, facets)

        patterns = self.allowed.get(who)
        if patterns is None:
            return Decision(False, f"未登记的写入方 {who!r}", self.mode, facets)
        if any(fnmatch.fnmatchcase(f, p) for f in facets for p in patterns):
            return Decision(True, f"{who} 的职责域允许", self.mode, facets)
        return Decision(False, f"{who} 的职责域不含 {list(facets)}", self.mode, facets)


def guard_shared_write(backend, agent_id: str | None, content: str,
                       memory_type: str = "concept",
                       tags: Iterable[str] | None = None) -> Optional[Decision]:
    """共享层写入的统一闸门 —— 必须在**真正落库之前**调用。

    行为分三档:
      · 未挂策略(独立使用适配层 / 老调用方)→ 直接返回 None,保持完全向后兼容。
      · audit / warn → 发 memory.shared.write 审计事件,不拦截。
      · enforce      → 未允许则抛 SharedWriteDenied。

    设计约束:审计**绝不能**影响写入本身 —— 事件失败被吞掉,只影响可观测性,不影响记忆落库。
    """
    policy = getattr(backend, "shared_write_policy", None)
    if policy is None:
        return None

    decision = policy.evaluate(agent_id, memory_type, tags)

    spine = getattr(backend, "shared_write_spine", None)
    if spine is not None:
        try:
            spine.publish("memory.shared.write", {
                "agent_id": agent_id or "",
                "type": memory_type,
                "tags": [t for t in (tags or ()) if isinstance(t, str)],
                "allowed": decision.allowed,
                "reason": decision.reason,
                "mode": decision.mode,
                # 只留摘要哈希 + 短预览,不把共享层正文原样灌进事件流
                "content_sha": hashlib.sha256(
                    str(content or "").encode("utf-8")).hexdigest()[:16],
                "content_preview": str(content or "")[:60],
            }, source=agent_id or "memory")
        except Exception:
            pass

    if not decision.allowed:
        if policy.mode == "enforce":
            raise SharedWriteDenied(agent_id or "unknown", decision.reason, content)
        if policy.mode == "warn":
            print(f"[Foxclaw] ⚠ 共享层越界写入(未拦截): agent={agent_id!r} "
                  f"reason={decision.reason}")
    return decision


def attach(backend, policy: SharedWritePolicy | None = None, spine=None) -> SharedWritePolicy:
    """把策略挂到记忆后端(HMRAdapter / MockHMR 皆可)。返回生效的策略实例。

    用挂载而非构造函数注入,是因为装配顺序:memory_adapter 在 event_spine **之前**创建,
    审计出口只有等脊柱建好才能挂上(见 foxclaw/__init__.py 的装配顺序)。
    """
    backend.shared_write_policy = policy or SharedWritePolicy()
    backend.shared_write_spine = spine
    return backend.shared_write_policy
