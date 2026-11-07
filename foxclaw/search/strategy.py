"""
Search Strategy —— 决定 continue / stop / switch_query / switch_source。
纯函数式判断(无状态、无 IO)，可独立单测和演进。
"""
from __future__ import annotations
from .models import EvidenceGraph, StrategyDecision, SubQuery

_CONFIDENCE_STOP_THRESHOLD = 0.75
_MAX_ITERATIONS = 3


class SearchStrategy:
    def decide(
        self,
        graph: EvidenceGraph,
        confidence: float,
        iteration: int,
        original_query: str,
    ) -> StrategyDecision:
        if confidence >= _CONFIDENCE_STOP_THRESHOLD:
            return StrategyDecision(action="stop", reason=f"置信度 {confidence:.2f} 已达标")

        if iteration >= _MAX_ITERATIONS:
            return StrategyDecision(action="stop", reason="达到最大迭代次数，防止无限循环")

        if graph.contradictions():
            return StrategyDecision(
                action="switch_source",
                reason="存在冲突证据，尝试切换更权威数据源",
                next_steps=[SubQuery(id=f"retry{iteration}", text=original_query)],
            )

        if len(graph.nodes) < 2:
            return StrategyDecision(
                action="switch_query",
                reason="证据不足，尝试改写查询",
                next_steps=[SubQuery(id=f"rephrase{iteration}", text=original_query + " 详细信息")],
            )

        return StrategyDecision(
            action="continue",
            reason="证据尚可但置信度不足，继续补充检索",
            next_steps=[SubQuery(id=f"more{iteration}", text=original_query)],
        )
