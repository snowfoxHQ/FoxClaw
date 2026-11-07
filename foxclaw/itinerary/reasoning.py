"""
Foxclaw 行程子系统 — 推理引擎(行程 Agent 的大脑,纯算法)
不依赖高德也能跑:路线优化(TSP)、可达性、时间冲突、地理聚类。
接入高德后,把"直线估算"替换为"真实路线时间"(见 runtime)。
"""

import math
from datetime import timedelta
from itertools import permutations

from .models import Waypoint, RouteLeg


# 各出行方式的估算平均速度(km/h),接高德后用真实数据替换
MODE_SPEED = {"walking": 5, "cycling": 15, "transit": 25, "driving": 35}


class ItineraryReasoning:
    """行程推理:把地点+时间变成可决策的行程智能。"""

    # ── 距离与可达性(直线估算)──────────────────────────
    @staticmethod
    def haversine_km(a: Waypoint, b: Waypoint) -> float:
        """两点直线距离(km)。Haversine 公式。"""
        if not (a.has_coord and b.has_coord):
            return 0.0
        R = 6371.0
        dlat = math.radians(b.lat - a.lat)
        dlng = math.radians(b.lng - a.lng)
        x = (math.sin(dlat/2)**2 +
             math.cos(math.radians(a.lat)) * math.cos(math.radians(b.lat)) *
             math.sin(dlng/2)**2)
        return R * 2 * math.atan2(math.sqrt(x), math.sqrt(1-x))

    @staticmethod
    def estimate_leg(a: Waypoint, b: Waypoint, mode: str = "driving") -> RouteLeg:
        """估算两点路线段(直线距离 × 1.3 绕路系数 / 速度)。"""
        straight = ItineraryReasoning.haversine_km(a, b)
        road_km = straight * 1.3                       # 绕路系数
        speed = MODE_SPEED.get(mode, 35)
        duration = (road_km / speed) * 60 if speed else 0
        return RouteLeg(
            from_id=a.id, to_id=b.id, from_name=a.name, to_name=b.name,
            mode=mode, distance_km=round(road_km, 2),
            duration_min=round(duration, 1), source="estimated",
        )

    # ── 路线优化(TSP)────────────────────────────────────
    @staticmethod
    def optimize_order(waypoints: list, mode: str = "driving",
                       fixed_start: bool = True) -> dict:
        """最优访问顺序。≤8 点用精确全排列,更多用最近邻启发式。
        fixed_start=True 时第一个点固定为起点(比如"从公司出发")。"""
        pts = [w for w in waypoints if w.has_coord]
        if len(pts) < 2:
            return {"order": [w.id for w in waypoints], "total_km": 0,
                    "total_min": 0, "method": "trivial"}

        def total_dist(seq):
            return sum(ItineraryReasoning.haversine_km(seq[i], seq[i+1])
                       for i in range(len(seq)-1))

        if len(pts) <= 8:
            # 精确解
            start = pts[0] if fixed_start else None
            rest = pts[1:] if fixed_start else pts
            best, best_d = None, float("inf")
            for perm in permutations(rest):
                seq = ([start] + list(perm)) if start else list(perm)
                d = total_dist(seq)
                if d < best_d:
                    best_d, best = d, seq
            method = "exact"
            ordered = best
        else:
            # 最近邻启发式
            remaining = pts[1:]
            ordered = [pts[0]]
            while remaining:
                last = ordered[-1]
                nxt = min(remaining, key=lambda w: ItineraryReasoning.haversine_km(last, w))
                ordered.append(nxt)
                remaining.remove(nxt)
            method = "nearest_neighbor"

        # 计算总距离/时间
        legs = [ItineraryReasoning.estimate_leg(ordered[i], ordered[i+1], mode)
                for i in range(len(ordered)-1)]
        return {
            "order": [w.id for w in ordered],
            "order_names": [w.name for w in ordered],
            "total_km": round(sum(l.distance_km for l in legs), 2),
            "total_min": round(sum(l.duration_min for l in legs), 1),
            "legs": [l.to_dict() for l in legs],
            "method": method,
        }

    # ── 时间冲突 + 可达性 ─────────────────────────────────
    @staticmethod
    def detect_conflicts(waypoints: list, mode: str = "driving") -> list:
        """按时间排序的行程,检测:① 时间重叠 ② 通勤赶不及。"""
        timed = [w for w in waypoints if w.dt is not None]
        timed.sort(key=lambda w: w.dt)
        issues = []
        for i in range(len(timed) - 1):
            cur, nxt = timed[i], timed[i+1]
            cur_end = cur.dt + timedelta(minutes=cur.duration_min)
            # 时间重叠
            if cur_end > nxt.dt:
                issues.append({
                    "type": "time_conflict",
                    "between": [cur.name, nxt.name],
                    "ids": [cur.id, nxt.id],
                    "reason": f"「{cur.name}」结束({cur_end.strftime('%H:%M')})"
                              f"晚于「{nxt.name}」开始({nxt.dt.strftime('%H:%M')})",
                })
                continue
            # 可达性:中间通勤时间够不够
            if cur.has_coord and nxt.has_coord:
                leg = ItineraryReasoning.estimate_leg(cur, nxt, mode)
                gap_min = (nxt.dt - cur_end).total_seconds() / 60
                if leg.duration_min > gap_min:
                    issues.append({
                        "type": "unreachable",
                        "between": [cur.name, nxt.name],
                        "ids": [cur.id, nxt.id],
                        "need_min": round(leg.duration_min, 0),
                        "available_min": round(gap_min, 0),
                        "reason": f"从「{cur.name}」到「{nxt.name}」需 {leg.duration_min:.0f} 分钟,"
                                  f"但只有 {gap_min:.0f} 分钟空档",
                    })
        return issues

    # ── 地理聚类(顺路归组)───────────────────────────────
    @staticmethod
    def cluster_by_proximity(waypoints: list, radius_km: float = 3.0) -> list:
        """把相近的行程点聚成簇,建议"顺路一起办"。"""
        pts = [w for w in waypoints if w.has_coord]
        clusters, used = [], set()
        for w in pts:
            if w.id in used:
                continue
            cluster = [w]
            used.add(w.id)
            for other in pts:
                if other.id in used:
                    continue
                if ItineraryReasoning.haversine_km(w, other) <= radius_km:
                    cluster.append(other)
                    used.add(other.id)
            clusters.append([c.name for c in cluster])
        return [c for c in clusters if len(c) > 1]   # 只返回有2+点的簇

    # ── 行程密度 ──────────────────────────────────────────
    @staticmethod
    def density(waypoints: list, mode: str = "driving") -> dict:
        """行程密度:总点数、总通勤、跨城。"""
        timed = sorted([w for w in waypoints if w.dt], key=lambda w: w.dt)
        cities = set(w.city for w in waypoints if w.city)
        total_commute = 0.0
        for i in range(len(timed) - 1):
            if timed[i].has_coord and timed[i+1].has_coord:
                total_commute += ItineraryReasoning.estimate_leg(
                    timed[i], timed[i+1], mode).duration_min
        span_hours = 0
        if len(timed) >= 2:
            span_hours = (timed[-1].dt - timed[0].dt).total_seconds() / 3600
        return {
            "waypoints": len(waypoints),
            "cities": list(cities),
            "cross_city": len(cities) > 1,
            "total_commute_min": round(total_commute, 1),
            "span_hours": round(span_hours, 1),
        }
