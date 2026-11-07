"""
B 阶段测试 — 行政 Agent 行程内核(行程行为操作系统)
验证:行程点 → 路线优化(TSP) → 冲突/可达检测 → 地理聚类 → 守门人
高德 API:有 AMAP_MAPS_API_KEY 用真实路线,否则直线估算。

注:本测试**不**断言具体公里数 —— 因为它随"是否配置高德 Key"而变(直线估算 vs 真实路线)。
断言的是与数据源无关的结构不变式(点数守恒、边数 = 点数-1、距离为正);具体数值只打印。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.itinerary import ItineraryRuntime

CSV = str(Path(__file__).parent.parent / "examples" / "sample_itinerary.csv")


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


def main():
    print("=" * 60)
    print("B 阶段 · 行政 Agent 行程内核(行程行为操作系统)")
    print("=" * 60)

    rt = ItineraryRuntime()
    n = rt.import_csv(CSV)
    print(f"\n采集 {n} 个行程点 · 高德接入: {rt.amap_enabled}")
    if not rt.amap_enabled:
        print("  (未设 AMAP_MAPS_API_KEY,使用直线距离估算;设置后自动用真实路线)")
    check(n == 5, f"采集 5 个行程点(实为 {n})")

    report = rt.plan(mode="driving")

    print("\n【路线优化(TSP)】")
    opt = report["optimized_route"]
    print(f"  方法: {opt['method']}")
    print(f"  最优顺序: {' → '.join(opt['order_names'])}")
    print(f"  总距离: {opt['total_km']} km · 总通勤: {opt['total_min']} 分钟")
    # 核心不变式:TSP 是"重排",不是"筛选" —— 所有行程点必须一个不少地出现在最优序里。
    # 这是最容易被静默破坏的一点(丢点不报错,只是路线变短)。
    check(len(opt["order_names"]) == n,
          f"最优路线包含全部 {n} 个点,无遗漏(实为 {len(opt['order_names'])} 个)")
    check(opt["total_km"] > 0 and opt["total_min"] > 0,
          f"总距离/总通勤为正数({opt['total_km']} km / {opt['total_min']} 分钟)")

    print("\n【时间冲突 / 可达性检测】")
    if report["conflicts"]:
        for c in report["conflicts"]:
            print(f"  [{c['type']}] {c['reason']}")
    else:
        print("  无冲突")
    ctypes = {c["type"] for c in report["conflicts"]}
    check("unreachable" in ctypes,
          f"检测出「可达性不足」冲突(样例数据 lunch→客户B 只有 30 分钟空档)(实为 {sorted(ctypes)})")

    print("\n【地理聚类(顺路归组)】")
    if report["proximity_clusters"]:
        for cl in report["proximity_clusters"]:
            print(f"  相近: {' · '.join(cl)}")
    else:
        print("  无明显聚集")
    check(len(report["proximity_clusters"]) >= 1,
          f"识别出 {len(report['proximity_clusters'])} 个可顺路归组的聚集")

    print("\n【行程密度】")
    d = report["density"]
    print(f"  {d['waypoints']} 个点 · 总通勤 {d['total_commute_min']} 分钟 · "
          f"跨城={d['cross_city']} · 时间跨度 {d['span_hours']} 小时")
    check(d["waypoints"] == n, f"密度统计点数与导入一致({d['waypoints']} == {n})")
    check(d["total_commute_min"] > 0 and d["span_hours"] > 0,
          f"通勤时长与时间跨度均为正({d['total_commute_min']} 分 / {d['span_hours']} 小时)")

    print("\n【行程守门人裁决】")
    for v in report["gatekeeper_verdicts"]:
        print(f"  [{v['action']}] {v['message']}")
    check(len(report["gatekeeper_verdicts"]) >= 1,
          f"守门人给出 {len(report['gatekeeper_verdicts'])} 条裁决")

    print("\n【高德可视化数据(MapTaskData)】")
    md = report["map_data"]
    pois = [x for x in md if x["type"] == "poi"]
    routes = [x for x in md if x["type"] == "route"]
    print(f"  {len(pois)} 个 POI 点 + {len(routes)} 条路线段")
    print("  → 可喂给高德 MCP 生成专属地图,导入高德 APP 一键导航")
    # 结构不变式:n 个点的高德图层 = n 个 POI + (n-1) 条连接段。少一段图就画不连贯。
    check(len(pois) == n, f"POI 点数 = 行程点数({len(pois)} == {n})")
    check(len(routes) == n - 1, f"路线段数 = 点数-1,首尾连贯({len(routes)} == {n - 1})")

    print("\n" + "=" * 60)
    print("✓ B 阶段验证完成:行程内核(路线优化+可达性+守门人+高德接口)成立")
    print("=" * 60)


def test_itinerary():
    main()


if __name__ == "__main__":
    main()
