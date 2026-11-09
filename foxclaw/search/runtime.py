"""
Foxclaw — Search: 认知搜索内核 (HSR)
=======================================
把"查内部记忆(HMR)"、"查内部知识(HKC)"和"查外部世界(联网搜索)"统一成
一次 search() 调用，供五个 Agent 共享。和 Finance / Learning / Itinerary
三个领域内核一样，遵循同一条边界：

    内核只产出证据 / 建议，不越权替调用方做决策。
    Finance 内核只建议、不动钱；Search 内核只给证据、不代替 Agent
    决定"这次搜索结果值不值得写回 HMR"——那是 Agent 自己的事
    (Agent 已经有 `_store_memory()`，Search 不需要再造一个)。

HKC 融合说明：
    HKC(foxclaw-hkc)现在是 Foxclaw 内部的一个模块，不是外挂的独立系统。
    `foxclaw/__init__.py` 在启动时会在同一个 Python 进程里直接装配
    HKCContainer，用 hkc_sdk.DirectClient 包成 HKCReadPort，再传进这里
    的 knowledge_port——全程没有 HTTP、没有第二个服务、没有端口。
    SearchRuntime 本身完全不关心 knowledge_port 具体是不是 HKC，
    只要实现了 query(text, top_k) 就行，这条边界保持干净。
"""
from __future__ import annotations
from typing import Optional

from .models import SearchResult, RawEvidence, SourceType, RouteType
from .planner import QueryPlanner
from .internal_search import InternalSearchEngine
from .external_search import BaiduWebSearchPort
from .retriever import Retriever
from .evidence import EvidenceFusion
from .strategy import SearchStrategy


class _HMRMemoryReadPort:
    """把 HMRAdapter / MockHMR 的 recall() 适配成 InternalSearchEngine
    需要的只读查询接口：query(text, top_k) -> list[RawEvidence]。

    故意只暴露 query，不暴露 ingest——HSR 内部检索只读 HMR，
    写不写记忆由 Agent 层的 `_store_memory()` 决定。"""

    def __init__(self, hmr, agent_id: Optional[str]):
        self.hmr = hmr
        self.agent_id = agent_id

    def query(self, text: str, top_k: int = 5) -> list[RawEvidence]:
        try:
            results = self.hmr.recall(query=text, agent_id=self.agent_id, top_k=top_k)
        except Exception:
            return []
        out: list[RawEvidence] = []
        for r in results or []:
            content = r.get("content", "") or r.get("title", "")
            if not content:
                continue
            conf = r.get("confidence", r.get("score", 0.6))
            out.append(RawEvidence(
                id=r.get("id", "") or f"hmr:{len(out)}",
                fact=content,
                source=SourceType.MEMORY,
                origin=f"hmr:{r.get('id', '')}",
                confidence=float(conf) if conf is not None else 0.6,
            ))
        return out


class SearchRuntime:
    """Foxclaw 的认知搜索内核。一次初始化，五个 Agent 共享调用
    （与 self.hmr / self.hkc / self.router / self.token_meter 同级）。"""

    def __init__(self, hmr=None, knowledge_port=None, agent_port=None,
                 external_ports: list | None = None):
        self.hmr = hmr
        # knowledge_port 由 foxclaw/__init__.py 在启动时构造好传进来
        # (进程内直连的 HKCReadPort)。这里不做任何自动连接/默认值——
        # 连不连 HKC、怎么连，是 Foxclaw 启动流程的事，不是 SearchRuntime 的事。
        self.knowledge_port = knowledge_port
        # agent_port:内部搜索的「Agent」源(领域内核 + 推理链 + 各Agent记忆 + 能力/状态),
        # 同样由 Foxclaw 启动时用各持有方引用构造好传进来。
        self.agent_port = agent_port
        self.planner = QueryPlanner()
        self.fusion = EvidenceFusion()
        self.strategy = SearchStrategy()
        # 外部端口(联网)面向 AI/Agent:是证据/工具管线的一环,不是给人的搜索框。
        self.external_ports = (
            external_ports if external_ports is not None else [BaiduWebSearchPort()]
        )

    def search(self, query: str, agent_id: str | None = None, context: dict | None = None,
               internal_only: bool = False) -> SearchResult:
        """唯一公开入口。给定 query，返回 SearchResult，过程中不写任何持久化状态。

        internal_only=True:只查内部(HMR记忆 + HKC知识 + Agent态势),跳过联网。
        供 Agent 反向调用内部搜索作工具时用(双向连通的「Agent→搜索」方向)。"""
        memory_port = _HMRMemoryReadPort(self.hmr, agent_id) if self.hmr else None
        internal = InternalSearchEngine(
            memory_port=memory_port, knowledge_port=self.knowledge_port,
            agent_port=self.agent_port,
        )
        external_ports = [] if internal_only else self.external_ports
        retriever = Retriever(internal_search=internal, external_ports=external_ports)

        trace: list[str] = []
        plan = self.planner.plan(query, context)
        if internal_only and plan.route == RouteType.EXTERNAL:
            # 只查内部时,把纯外部路由拉回内部,保证内部三源(记忆/知识/Agent)被检索
            plan.route = RouteType.INTERNAL
            trace.append("[internal_only] 路由改写为 HSR-I(仅内部)")
        trace.append(f"[plan] {plan.reasoning}")

        raw = retriever.retrieve(plan.steps, plan.route)
        graph = self.fusion.build(raw)
        confidence = self.fusion.confidence(graph)

        iteration = 0
        while True:
            decision = self.strategy.decide(graph, confidence, iteration, query)
            trace.append(f"[loop {iteration}] {decision.action}: {decision.reason}")

            if decision.action == "stop":
                break

            more = retriever.retrieve(decision.next_steps, plan.route)
            if not more:
                trace.append(f"[loop {iteration}] 无新证据，提前停止")
                break

            raw += more
            graph = self.fusion.build(raw)
            confidence = self.fusion.confidence(graph)
            iteration += 1

        return SearchResult(
            query=query, route=plan.route, graph=graph,
            iterations=iteration, confidence=confidence, trace=trace,
        )
