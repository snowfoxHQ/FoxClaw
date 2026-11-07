"""
Foxclaw 行程子系统 — 数据模型
行程行为操作系统的结构:
  Waypoint(行程点:地点+时间+坐标)、RouteLeg(路线段)、
  ItineraryVerdict(行程守门人裁决)
核心思想:行程 = 时空中的可计算约束(地理位置 + 时间窗口 + 可达性)。
坐标格式遵循高德:经度在前,纬度在后 [lng, lat]。
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Literal
import uuid


TravelMode = Literal["walking", "driving", "transit", "cycling"]


@dataclass
class Waypoint:
    """一个行程点 = 时空中的一个约束"""
    name: str                           # 地点名:公司、客户A、机场
    address: str = ""                   # 地址(可经地理编码转坐标)
    lng: Optional[float] = None         # 经度(高德:经度在前)
    lat: Optional[float] = None         # 纬度
    start_time: Optional[str] = None    # 计划到达时间 ISO
    duration_min: int = 0               # 停留时长(分钟)
    city: str = ""
    note: str = ""
    id: str = field(default_factory=lambda: f"wp_{uuid.uuid4().hex[:10]}")

    @property
    def has_coord(self) -> bool:
        return self.lng is not None and self.lat is not None

    @property
    def coord_str(self) -> str:
        """高德格式坐标字符串 '经度,纬度'"""
        return f"{self.lng},{self.lat}" if self.has_coord else ""

    @property
    def dt(self) -> Optional[datetime]:
        return datetime.fromisoformat(self.start_time) if self.start_time else None

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(has_coord=self.has_coord, coord_str=self.coord_str)
        return d


@dataclass
class RouteLeg:
    """两点之间的路线段"""
    from_id: str
    to_id: str
    from_name: str
    to_name: str
    mode: TravelMode = "driving"
    distance_km: float = 0.0
    duration_min: float = 0.0
    source: Literal["estimated", "amap"] = "estimated"  # 估算 or 高德真实
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ItineraryVerdict:
    """行程守门人裁决(对应财务/学习守门人)"""
    rule_id: str
    action: Literal["time_conflict", "unreachable", "too_dense",
                    "cross_city", "suggest_reorder"]
    message: str
    severity: Literal["info", "warn", "critical"] = "warn"
    waypoint_ids: list = field(default_factory=list)
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
