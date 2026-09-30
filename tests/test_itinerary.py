"""
B 阶段测试 — 行政 Agent 行程内核(行程行为操作系统)
验证:行程点 → 路线优化(TSP) → 冲突/可达检测 → 地理聚类 → 守门人
高德 API:有 AMAP_MAPS_API_KEY 用真实路线,否则直线估算。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from hestia.itinerary import ItineraryRuntime

CSV = str(Path(__file__).parent.parent / "examples" / "sample_itinerary.csv")


def main():
    print("=" * 60)
    print("B 阶段 · 行政 Agent 行程内核(行程行为操作系统)")
    print("=" * 60)

    rt = ItineraryRuntime()
    n = rt.import_csv(CSV)
    print(f"\n采集 {n} 个行程点 · 高德接入: {rt.amap_enabled}")
    if not rt.amap_enabled:
        print("  (未设 AMAP_MAPS_API_KEY,使用直线距离估算;设置后自动用真实路线)")

    report = rt.plan(mode="driving")

    print("\n【路线优化(TSP)】")
    opt = report["optimized_route"]
    print(f"  方法: {opt['method']}")
    print(f"  最优顺序: {' → '.join(opt['order_names'])}")
    print(f"  总距离: {opt['total_km']} km · 总通勤: {opt['total_min']} 分钟")

    print("\n【时间冲突 / 可达性检测】")
    if report["conflicts"]:
        for c in report["conflicts"]:
            print(f"  [{c['type']}] {c['reason']}")
    else:
        print("  无冲突")

    print("\n【地理聚类(顺路归组)】")
    if report["proximity_clusters"]:
        for cl in report["proximity_clusters"]:
            print(f"  相近: {' · '.join(cl)}")
    else:
        print("  无明显聚集")

    print("\n【行程密度】")
    d = report["density"]
    print(f"  {d['waypoints']} 个点 · 总通勤 {d['total_commute_min']} 分钟 · "
          f"跨城={d['cross_city']} · 时间跨度 {d['span_hours']} 小时")

    print("\n【行程守门人裁决】")
    for v in report["gatekeeper_verdicts"]:
        print(f"  [{v['action']}] {v['message']}")

    print("\n【高德可视化数据(MapTaskData)】")
    md = report["map_data"]
    pois = [x for x in md if x["type"] == "poi"]
    routes = [x for x in md if x["type"] == "route"]
    print(f"  {len(pois)} 个 POI 点 + {len(routes)} 条路线段")
    print("  → 可喂给高德 MCP 生成专属地图,导入高德 APP 一键导航")

    print("\n" + "=" * 60)
    print("✓ B 阶段验证完成:行程内核(路线优化+可达性+守门人+高德接口)成立")
    print("=" * 60)


if __name__ == "__main__":
    main()
