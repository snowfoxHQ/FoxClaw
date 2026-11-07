"""
Foxclaw 行程子系统 — Itinerary Runtime
行程行为操作系统:采集行程点 → 地理编码(高德或手动) → 路线优化 →
冲突/可达检测 → 行程守门人。高德可用时自动用真实路线替换估算。
"""

import csv
from pathlib import Path

from .models import Waypoint, ItineraryVerdict
from .reasoning import ItineraryReasoning
from .amap_client import AMapClient


class ItineraryRuntime:
    """行政 Agent 的行程内核。"""

    def __init__(self, amap_key: str = None, hmr=None):
        self.waypoints: list = []
        self.reasoning = ItineraryReasoning()
        self.amap = AMapClient(amap_key)
        self.hmr = hmr

    @property
    def amap_enabled(self) -> bool:
        return self.amap.enabled

    # ── 采集行程点 ────────────────────────────────────────
    def add_waypoint(self, name: str, address: str = "", lng: float = None,
                     lat: float = None, start_time: str = None,
                     duration_min: int = 0, city: str = "") -> dict:
        wp = Waypoint(name=name, address=address, lng=lng, lat=lat,
                      start_time=start_time, duration_min=duration_min, city=city)
        # 没坐标但有地址 → 尝试高德地理编码
        if not wp.has_coord and address and self.amap_enabled:
            geo = self.amap.geocode(address, city)
            if geo:
                wp.lng, wp.lat = geo
        self.waypoints.append(wp)
        if self.hmr:
            try:
                self.hmr.ingest(content=f"行程:{name} @ {address or wp.coord_str} "
                                        f"{start_time or ''}",
                                type="task", agent_id="office",
                                title=f"行程_{name}", tags=["itinerary"])
            except Exception:
                pass
        return wp.to_dict()

    def import_csv(self, path: str) -> int:
        """CSV 导入。列:name,address,lng,lat,start_time,duration_min,city"""
        count = 0
        with open(Path(path), encoding="utf-8") as f:
            for row in csv.DictReader(f):
                self.add_waypoint(
                    name=row["name"], address=row.get("address", ""),
                    lng=float(row["lng"]) if row.get("lng") else None,
                    lat=float(row["lat"]) if row.get("lat") else None,
                    start_time=row.get("start_time") or None,
                    duration_min=int(row.get("duration_min") or 0),
                    city=row.get("city", ""))
                count += 1
        return count

    # ── 真实路线(高德优先,否则估算)─────────────────────
    def real_leg(self, a: Waypoint, b: Waypoint, mode: str = "driving"):
        """两点路线:高德可用且有坐标时用真实数据,否则直线估算。"""
        if self.amap_enabled and a.has_coord and b.has_coord:
            r = self.amap.route(a.coord_str, b.coord_str, mode, a.city)
            if r:
                from .models import RouteLeg
                return RouteLeg(from_id=a.id, to_id=b.id, from_name=a.name,
                                to_name=b.name, mode=mode,
                                distance_km=r["distance_km"],
                                duration_min=r["duration_min"], source="amap")
        return self.reasoning.estimate_leg(a, b, mode)

    # ── 综合分析(行政 Agent 核心输出)────────────────────
    def plan(self, mode: str = "driving", optimize: bool = True) -> dict:
        """完整行程规划:优化顺序 + 冲突检测 + 聚类 + 守门人裁决。"""
        opt = (self.reasoning.optimize_order(self.waypoints, mode)
               if optimize else None)
        conflicts = self.reasoning.detect_conflicts(self.waypoints, mode)
        clusters = self.reasoning.cluster_by_proximity(self.waypoints)
        dens = self.reasoning.density(self.waypoints, mode)
        verdicts = self._gatekeeper(conflicts, clusters, dens)
        return {
            "waypoint_count": len(self.waypoints),
            "amap_enabled": self.amap_enabled,
            "optimized_route": opt,
            "conflicts": conflicts,
            "proximity_clusters": clusters,
            "density": dens,
            "gatekeeper_verdicts": [v.to_dict() for v in verdicts],
            "map_data": self._to_amap_mapdata(opt),
        }

    # ── 行程守门人 ────────────────────────────────────────
    def _gatekeeper(self, conflicts, clusters, dens) -> list:
        verdicts = []
        for c in conflicts:
            if c["type"] == "time_conflict":
                verdicts.append(ItineraryVerdict(
                    rule_id="time_conflict", action="time_conflict",
                    severity="critical", message=c["reason"], waypoint_ids=c["ids"]))
            elif c["type"] == "unreachable":
                verdicts.append(ItineraryVerdict(
                    rule_id="unreachable", action="unreachable",
                    severity="critical", message=c["reason"], waypoint_ids=c["ids"]))
        if dens["cross_city"]:
            verdicts.append(ItineraryVerdict(
                rule_id="cross_city", action="cross_city", severity="warn",
                message=f"行程跨城市:{','.join(dens['cities'])},注意城际交通时间",
                payload={"cities": dens["cities"]}))
        if dens["total_commute_min"] > 180:
            verdicts.append(ItineraryVerdict(
                rule_id="too_dense", action="too_dense", severity="warn",
                message=f"总通勤 {dens['total_commute_min']:.0f} 分钟过长,建议精简或分天",
                payload=dens))
        for cl in clusters:
            verdicts.append(ItineraryVerdict(
                rule_id="reorder", action="suggest_reorder", severity="info",
                message=f"这几处相近,建议顺路一起办:{' · '.join(cl)}",
                payload={"cluster": cl}))
        return verdicts

    # ── 导出高德可视化格式(MapTaskData)──────────────────
    def _to_amap_mapdata(self, opt) -> list:
        """转成高德 MapTaskData(POI + route),可喂给高德生成可视化地图。"""
        data = []
        for wp in self.waypoints:
            if wp.has_coord:
                data.append({"type": "poi", "lnglat": [wp.lng, wp.lat],
                             "text": wp.name, "remark": wp.address or wp.note})
        if opt and opt.get("legs"):
            id2wp = {w.id: w for w in self.waypoints}
            for leg in opt["legs"]:
                a, b = id2wp.get(leg["from_id"]), id2wp.get(leg["to_id"])
                if a and b and a.has_coord and b.has_coord:
                    data.append({"type": "route", "routeType": leg["mode"],
                                 "start": [a.lng, a.lat], "end": [b.lng, b.lat],
                                 "remark": f"{a.name}→{b.name}"})
        return data

    def status(self) -> dict:
        return {"waypoints": len(self.waypoints),
                "amap_enabled": self.amap_enabled,
                "with_coord": sum(1 for w in self.waypoints if w.has_coord)}
