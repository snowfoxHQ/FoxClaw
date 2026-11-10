"""
Foxclaw — Search 内核数据模型 (HSR)

设计原则：这一层只产出"证据"，不做任何持久化决策。
是否把搜索结果沉淀回 HMR、是否提交知识库(未来的 foxclaw-hkc)，
由调用方(Agent / Runtime)决定，这里的数据结构都是只读产物。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class RouteType(str, Enum):
    INTERNAL = "HSR-I"
    EXTERNAL = "HSR-E"
    HYBRID = "HYBRID"


class SourceType(str, Enum):
    MEMORY = "memory"
    KNOWLEDGE = "knowledge"
    AGENT = "agent"
    WEB = "web"


@dataclass
class SubQuery:
    id: str
    text: str
    source_hint: Optional[SourceType] = None
    depends_on: list[str] = field(default_factory=list)


@dataclass
class SearchPlan:
    route: RouteType
    steps: list[SubQuery]
    reasoning: str = ""


@dataclass
class RawEvidence:
    id: str
    fact: str
    source: SourceType
    origin: str
    confidence: float = 0.5
    raw: Any = None
    grounded_by: Optional[str] = None


@dataclass
class EvidenceNode:
    id: str
    fact: str
    source: SourceType
    origin: str
    confidence: float
    grounded_by: Optional[str] = None


@dataclass
class EvidenceEdge:
    src: str
    dst: str
    relation: str


@dataclass
class EvidenceGraph:
    nodes: dict[str, EvidenceNode] = field(default_factory=dict)
    edges: list[EvidenceEdge] = field(default_factory=list)

    def add_node(self, node: EvidenceNode) -> None:
        self.nodes[node.id] = node

    def add_edge(self, edge: EvidenceEdge) -> None:
        self.edges.append(edge)

    def contradictions(self) -> list[EvidenceEdge]:
        return [e for e in self.edges if e.relation == "contradicts"]

    def to_dict(self) -> dict:
        """给 API / ThoughtChain 用的精简序列化。"""
        return {
            "nodes": [
                {
                    "id": n.id, "fact": n.fact, "source": n.source.value,
                    "origin": n.origin, "confidence": round(n.confidence, 2),
                    "grounded_by": n.grounded_by,
                }
                for n in self.nodes.values()
            ],
            "edges": [
                {"src": e.src, "dst": e.dst, "relation": e.relation}
                for e in self.edges
            ],
        }


@dataclass
class StrategyDecision:
    action: str
    reason: str
    next_steps: list[SubQuery] = field(default_factory=list)


@dataclass
class SearchResult:
    """search() 的唯一返回值。到这里为止，Search 内核不再做任何事。"""
    query: str
    route: RouteType
    graph: EvidenceGraph
    iterations: int
    confidence: float
    trace: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "route": self.route.value,
            "confidence": round(self.confidence, 2),
            "iterations": self.iterations,
            "graph": self.graph.to_dict(),
            "trace": self.trace,
        }
