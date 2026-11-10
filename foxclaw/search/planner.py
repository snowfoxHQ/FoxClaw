"""
Query Planner —— 决定路由(HSR-I / HSR-E / HYBRID)，并把任务拆成多跳检索步骤。

当前是规则实现(触发词 + 标点拆句)，接口(SearchPlan)保持稳定，
未来要换成 LLM function-calling 结构化拆解时，只需替换这个文件内部实现，
不影响 Retriever / EvidenceFusion / SearchStrategy。
"""
from __future__ import annotations
from .models import SearchPlan, SubQuery, RouteType
from ..planning import planner as P

_MEMORY_HINTS = ("我之前", "我们讨论过", "上次", "记得", "history", "之前说")
_HYBRID_HINTS = ("为什么", "对比", "分析", "综合", "评估")


class QueryPlanner:
    def route(self, query: str, context: dict | None = None) -> RouteType:
        if any(h in query for h in _MEMORY_HINTS):
            return RouteType.INTERNAL
        if any(h in query for h in _HYBRID_HINTS):
            return RouteType.HYBRID
        return RouteType.EXTERNAL

    def plan(self, query: str, context: dict | None = None) -> SearchPlan:
        route = self.route(query, context)

        if route == RouteType.INTERNAL:
            steps = [SubQuery(id="s1", text=query)]
        elif route == RouteType.EXTERNAL:
            steps = self._decompose(query)
        else:
            steps = [SubQuery(id="s1", text=query), *self._decompose(query, offset=2)]

        return SearchPlan(
            route=route,
            steps=steps,
            reasoning=f"route={route.value}, steps={len(steps)}",
        )

    def _decompose(self, query: str, offset: int = 1) -> list[SubQuery]:
        parts = P.split_on(query, P.LIST_SEPS)
        if len(parts) <= 1:
            parts = [query]
        return [SubQuery(id=f"s{offset + i}", text=p) for i, p in enumerate(parts)]
