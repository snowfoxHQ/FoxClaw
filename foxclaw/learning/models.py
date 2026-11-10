"""
Foxclaw 学习子系统 — 数据模型
学习行为操作系统的四类结构:
  KnowledgeItem(知识条目)、ReviewState(间隔重复状态)、
  KnowledgeEdge(知识图谱边)、LearningVerdict(学习策略裁决)
核心思想:学习 = 可追踪的记忆行为轨迹(纵向掌握度 + 横向知识连接)。
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Literal
import uuid


EdgeType = Literal["same_topic", "concept_ref", "semantic", "manual"]
Grade = Literal[0, 1, 2, 3, 4, 5]


@dataclass
class KnowledgeItem:
    """一个知识条目 = 一次学习采集"""
    content: str
    topic: str = "general"
    source: str = ""
    tags: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    id: str = field(default_factory=lambda: f"ki_{uuid.uuid4().hex[:10]}")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReviewState:
    """SM-2 间隔重复状态(固化层核心)"""
    item_id: str
    repetitions: int = 0
    interval: int = 0
    ease_factor: float = 2.5
    due_date: str = field(default_factory=lambda: datetime.now().isoformat())
    last_review: Optional[str] = None
    total_reviews: int = 0
    lapses: int = 0

    @property
    def mastery(self) -> float:
        """掌握度 0~1:综合连续正确次数和难度因子。"""
        rep_score = min(self.repetitions / 6.0, 1.0)
        ease_score = (self.ease_factor - 1.3) / (2.5 - 1.3)
        ease_score = max(0.0, min(1.0, ease_score))
        return round(0.6 * rep_score + 0.4 * ease_score, 3)

    @property
    def is_due(self) -> bool:
        return datetime.fromisoformat(self.due_date) <= datetime.now()

    @property
    def days_until_due(self) -> int:
        delta = datetime.fromisoformat(self.due_date) - datetime.now()
        return delta.days

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(mastery=self.mastery, is_due=self.is_due,
                 days_until_due=self.days_until_due)
        return d


@dataclass
class KnowledgeEdge:
    """知识图谱的一条边(连接层核心)"""
    source_id: str
    target_id: str
    edge_type: EdgeType
    weight: float = 1.0
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LearningVerdict:
    """学习策略裁决(对应财务守门人)"""
    rule_id: str
    action: Literal["review_due", "weak_topic_alert", "overload_warn", "suggest_connect"]
    message: str
    severity: Literal["info", "warn"] = "info"
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
