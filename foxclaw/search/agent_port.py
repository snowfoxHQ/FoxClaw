"""
AgentSourcePort —— 内部搜索的「Agent」源。

把五个 Agent 持有的**全部内部数据**连通进 HSR 的内部检索，四类子源:
  1. 领域内核结构化数据:财务(交易/预算/实体) + 学习(知识条目) + 行程(行程点)
  2. ThoughtChain 推理链:各 Agent 想过/判过什么(按内容关键词检索)
  3. 各 Agent 的 HMR 记忆:逐 agent_id 召回(区别于共享 memory_port 的当前-agent 召回)
  4. 能力/状态目录:skill 目录 + 各 Agent 角色/状态("哪个 Agent 能做这事")

设计边界(与 HSR 一致):
  · 只读——绝不写回任何持有方状态。
  · 解耦——只依赖各持有方稳定的公开读接口 + 领域对象文本化,不侵入其内部逻辑。
  · 降级——任一子源缺失/报错都 try/except 跳过,不影响其它子源与主流程。
  · 只依赖显式传入的引用(不 import Foxclaw),保持 search 内核与本体的低耦合。
"""
from __future__ import annotations
import re
from .models import RawEvidence, SourceType


def _score(query: str, text: str) -> int:
    """轻量关键词打分:词级(空格/标点分隔,len>=2 子串命中)+ 中文 2-gram 重叠。
    纯确定性、可解释,不引入模型——语义匹配由 HKC/HMR 端负责,这里是结构化态势查找。"""
    q = (query or "").strip()
    t = text or ""
    if not q or not t:
        return 0
    score = 0
    for term in re.split(r"[\s,，、;；:：/]+", q):
        if len(term) >= 2 and term in t:
            score += 2
    for i in range(len(q) - 1):          # 中文/无分隔场景的 2-gram 重叠
        if q[i:i + 2] in t:
            score += 1
    return score


def _conf(score: int) -> float:
    return round(min(0.7, 0.4 + 0.04 * score), 3)


class AgentSourcePort:
    """内部搜索的 Agent 源。query(text, top_k) -> list[RawEvidence(source=AGENT)]。"""

    def __init__(self, agents=None, finance_runtime=None, learning_runtime=None,
                 itinerary_runtime=None, thought_chain=None, skill_registry=None,
                 hmr=None):
        self.agents = agents or {}
        self.finance = finance_runtime
        self.learning = learning_runtime
        self.itinerary = itinerary_runtime
        self.thought_chain = thought_chain
        self.skill_registry = skill_registry
        self.hmr = hmr

    @property
    def available(self) -> bool:
        return any([self.agents, self.finance, self.learning, self.itinerary,
                    self.thought_chain, self.skill_registry, self.hmr])

    def query(self, text: str, top_k: int = 5) -> list[RawEvidence]:
        scored: list[tuple[int, RawEvidence]] = []
        for gather in (self._from_finance, self._from_learning, self._from_itinerary,
                       self._from_thoughtchain, self._from_memories, self._from_capabilities):
            try:
                scored += gather(text, top_k)
            except Exception:
                continue  # 单一子源失败不影响其它子源
        scored.sort(key=lambda x: x[0], reverse=True)
        return [ev for _, ev in scored[:max(top_k * 2, top_k)]]

    # ── 1. 领域内核 ──────────────────────────────────────────
    def _from_finance(self, text, top_k):
        out: list[tuple[float, RawEvidence]] = []
        rt = self.finance
        if rt is None:
            return out
        for i, txn in enumerate(getattr(rt, "transactions", []) or []):
            desc = f"交易 {getattr(txn,'entity','')} {getattr(txn,'amount','')} " \
                   f"{getattr(txn,'category','')} {' '.join(getattr(txn,'tags',[]) or [])}"
            s = _score(text, desc)
            if s:
                out.append((s, RawEvidence(id=f"agent:finance:txn:{i}", fact=desc.strip(),
                            source=SourceType.AGENT, origin="agent:finance:transaction",
                            confidence=_conf(s))))
        for i, b in enumerate(getattr(rt, "budgets", []) or []):
            desc = f"预算 {getattr(b,'category','')} 上限 {getattr(b,'monthly_limit','')}"
            s = _score(text, desc)
            if s:
                out.append((s, RawEvidence(id=f"agent:finance:budget:{i}", fact=desc.strip(),
                            source=SourceType.AGENT, origin="agent:finance:budget",
                            confidence=_conf(s))))
        for name, ent in (getattr(rt, "entities", {}) or {}).items():
            desc = f"财务实体 {name} 累计 {getattr(ent,'total_amount','')} " \
                   f"次数 {getattr(ent,'txn_count','')}"
            s = _score(text, desc)
            if s:
                out.append((s, RawEvidence(id=f"agent:finance:entity:{name}", fact=desc.strip(),
                            source=SourceType.AGENT, origin="agent:finance:entity",
                            confidence=_conf(s))))
        return out

    def _from_learning(self, text, top_k):
        out: list[tuple[float, RawEvidence]] = []
        rt = self.learning
        if rt is None:
            return out
        for iid, item in (getattr(rt, "items", {}) or {}).items():
            desc = f"知识 {getattr(item,'content','')} 主题 {getattr(item,'topic','')} " \
                   f"{' '.join(getattr(item,'tags',[]) or [])}"
            s = _score(text, desc)
            if s:
                out.append((s, RawEvidence(id=f"agent:learning:{iid}", fact=desc.strip(),
                            source=SourceType.AGENT, origin=f"agent:learning:{iid}",
                            confidence=_conf(s))))
        return out

    def _from_itinerary(self, text, top_k):
        out: list[tuple[float, RawEvidence]] = []
        rt = self.itinerary
        if rt is None:
            return out
        for i, wp in enumerate(getattr(rt, "waypoints", []) or []):
            desc = f"行程点 {getattr(wp,'name','')} {getattr(wp,'address','')} " \
                   f"{getattr(wp,'city','')} {getattr(wp,'start_time','') or ''}"
            s = _score(text, desc)
            if s:
                out.append((s, RawEvidence(id=f"agent:itinerary:{i}", fact=desc.strip(),
                            source=SourceType.AGENT, origin="agent:itinerary:waypoint",
                            confidence=_conf(s))))
        return out

    # ── 2. ThoughtChain 推理链 ───────────────────────────────
    def _from_thoughtchain(self, text, top_k):
        out: list[tuple[float, RawEvidence]] = []
        tc = self.thought_chain
        if tc is None or not hasattr(tc, "search_nodes"):
            return out
        for n in tc.search_nodes(text, limit=top_k * 2):
            content = getattr(n, "content", "")
            s = _score(text, content)
            out.append((max(s, 1), RawEvidence(
                id=f"agent:think:{getattr(n,'id','')}", fact=f"推理[{getattr(n,'agent_id','')}] {content}",
                source=SourceType.AGENT, origin=f"agent:thoughtchain:{getattr(n,'agent_id','')}",
                confidence=_conf(max(s, 1)))))
        return out

    # ── 3. 各 Agent 的 HMR 记忆 ─────────────────────────────
    def _from_memories(self, text, top_k):
        out: list[tuple[float, RawEvidence]] = []
        if self.hmr is None:
            return out
        for aid in (self.agents.keys() if self.agents else []):
            try:
                results = self.hmr.recall(query=text, agent_id=aid, top_k=top_k)
            except Exception:
                continue
            for r in results or []:
                content = r.get("content", "") or r.get("title", "")
                if not content:
                    continue
                s = _score(text, content) or 1
                out.append((s, RawEvidence(
                    id=f"agent:mem:{aid}:{r.get('id','')}",
                    fact=f"[{aid}记忆] {content}", source=SourceType.AGENT,
                    origin=f"agent:{aid}:hmr:{r.get('id','')}",
                    confidence=_conf(s))))
        return out

    # ── 4. 能力/状态目录 ─────────────────────────────────────
    def _from_capabilities(self, text, top_k):
        out = []
        reg = self.skill_registry
        if reg is not None and hasattr(reg, "catalog"):
            for sk in reg.catalog():
                desc = f"技能 {sk.get('name','')} 触发 {','.join(sk.get('triggers',[]) or [])} " \
                       f"{sk.get('kind','')}/{sk.get('side_effect','')}"
                s = _score(text, desc)
                if s:
                    out.append((s, RawEvidence(id=f"agent:skill:{sk.get('name','')}",
                                fact=desc.strip(), source=SourceType.AGENT,
                                origin="agent:capability:skill", confidence=_conf(s))))
        for aid, agent in (self.agents or {}).items():
            try:
                st = agent.get_status()
            except Exception:
                st = {}
            desc = f"Agent {aid} 角色 {st.get('role', st.get('name',''))} 状态 {st.get('status','')}"
            s = _score(text, desc)
            if s:
                out.append((s, RawEvidence(id=f"agent:status:{aid}", fact=desc.strip(),
                            source=SourceType.AGENT, origin=f"agent:{aid}:status",
                            confidence=_conf(s))))
        return out
