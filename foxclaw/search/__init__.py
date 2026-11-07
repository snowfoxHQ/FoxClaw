"""Foxclaw — Search: 认知搜索内核 (HSR)
统一内部记忆(HMR)与外部联网检索，供五个 Agent 共享调用。"""
from .models import (
    RouteType, SourceType, SubQuery, SearchPlan,
    RawEvidence, EvidenceNode, EvidenceEdge, EvidenceGraph,
    StrategyDecision, SearchResult,
)
from .planner import QueryPlanner
from .internal_search import InternalSearchEngine
from .external_search import BaiduWebSearchPort
from .hkc_read_port import HKCReadPort
from .agent_port import AgentSourcePort
from .retriever import Retriever
from .evidence import EvidenceFusion
from .strategy import SearchStrategy
from .runtime import SearchRuntime

__all__ = [
    "RouteType", "SourceType", "SubQuery", "SearchPlan",
    "RawEvidence", "EvidenceNode", "EvidenceEdge", "EvidenceGraph",
    "StrategyDecision", "SearchResult",
    "QueryPlanner", "InternalSearchEngine", "BaiduWebSearchPort", "HKCReadPort",
    "AgentSourcePort", "Retriever", "EvidenceFusion", "SearchStrategy", "SearchRuntime",
]
