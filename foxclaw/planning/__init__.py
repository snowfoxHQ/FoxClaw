"""
foxclaw.planning —— 统一规划原语(P1-2)。

三处路由(主管→子Agent / AHS→skill / search→数据源)共用的机制单一真源:
否定词检测、标点拆分、marker 常量、触发词打分。
"""
from .planner import (
    NEGATIONS, SPLIT_MARKERS, PARALLEL_TRIGGERS, LIST_SEPS,
    is_negated, split_on, keyword_hits,
    ScoredCandidate, TriggerRoute, trigger_route,
)

__all__ = [
    "NEGATIONS", "SPLIT_MARKERS", "PARALLEL_TRIGGERS", "LIST_SEPS",
    "is_negated", "split_on", "keyword_hits",
    "ScoredCandidate", "TriggerRoute", "trigger_route",
]
