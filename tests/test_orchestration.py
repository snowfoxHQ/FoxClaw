"""
主管编排测试 — 全局态势 + 跨 Agent 编排
核心场景:行程 Agent 规划出差 → 主管发起差旅预算申请 →
         财务守门人裁决(够不够/要不要确认/否决)。
这把三个独立内核真正串成一个有机系统。
"""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["FOXCLAW_SHADOW_ROUNDS"] = "50"

import asyncio
from foxclaw import Foxclaw


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


async def main():
    print("=" * 60)
    print("主管编排 · 全局态势 + 跨 Agent 协调")
    print("=" * 60)
    _data = tempfile.mkdtemp(prefix="foxclaw_orch_")
    os_ = Foxclaw(data_dir=_data, shadow_rounds=50)

    fin = os_.finance_runtime
    fin.set_budget("travel", 3000)
    fin.set_balance("default", 5000)
    fin.import_csv(str(Path(__file__).parent.parent / "examples" / "sample_transactions.csv"))

    learn = os_.learning_runtime
    learn.import_csv(str(Path(__file__).parent.parent / "examples" / "sample_knowledge.csv"))

    itin = os_.itinerary_runtime
    itin.import_csv(str(Path(__file__).parent.parent / "examples" / "sample_itinerary.csv"))

    print("\n【1】主管全局态势(驾驶舱视图)")
    sit = os_.supervisor.global_situation()
    print(f"  财务: {sit['finance']}")
    print(f"  学习: {sit['learning']}")
    print(f"  行程: {sit['itinerary']}")
    print(f"  跨域告警 {len(sit['alerts'])} 条:")
    for a in sit["alerts"][:5]:
        print(f"    [{a['severity']}] ({a['domain']}) {a['msg']}")
    check(sit["finance"]["transactions"] == 13, f"财务态势反映 13 笔导入交易(实为 {sit['finance']['transactions']})")
    check(sit["learning"]["total_items"] == 8, f"学习态势反映 8 个知识点(实为 {sit['learning']['total_items']})")
    check(sit["itinerary"]["waypoints"] == 5, f"行程态势反映 5 个行程点(实为 {sit['itinerary']['waypoints']})")
    sev = {a["severity"] for a in sit["alerts"]}
    check(len(sit["alerts"]) >= 2 and "critical" in sev,
          f"跨域告警被汇总且含 critical(实为 {sorted(sev)})")

    print("\n【2】跨 Agent 编排:行程→财务 差旅预算申请(小额)")
    r1 = os_.supervisor.cross_request(
        from_agent="admin", to_agent="finance", request_type="budget_request",
        payload={"amount": 150, "category": "travel", "entity": "高铁票", "account": "default"},
        reason="出差北京高铁票")
    print(f"  申请: 差旅 150 元 → 状态【{r1['status']}】")
    print(f"  财务裁决: {r1['verdict']}")
    check(r1["status"] == "approved",
          f"小额(150)且在预算内 → 直接批准(实为 {r1['status']})")

    print("\n【3】跨 Agent 编排:大额差旅(触发守门人)")
    r2 = os_.supervisor.cross_request(
        from_agent="admin", to_agent="finance", request_type="budget_request",
        payload={"amount": 680, "category": "travel", "entity": "商务酒店", "account": "default"},
        reason="出差住宿3晚")
    print(f"  申请: 差旅 680 元 → 状态【{r2['status']}】")
    print(f"  财务裁决: {r2['verdict']}")
    check(r2["status"] == "needs_confirmation",
          f"大额(680>500) → 转人工确认(实为 {r2['status']})")
    check(r1["status"] != r2["status"], "金额不同 → 裁决结论确实不同(150 批准 / 680 待确认)")

    print("\n【4】跨 Agent 编排:日程占用申请→行政检查冲突")
    r3 = os_.supervisor.cross_request(
        from_agent="assistant", to_agent="admin", request_type="schedule_request",
        payload={"start_time": "2026-06-10T10:30:00", "title": "临时电话会"},
        reason="客户临时约电话会")
    print(f"  申请: 10:30 电话会 → 状态【{r3['status']}】")
    print(f"  行政裁决: {r3['verdict']}")
    check(r3["status"] == "needs_confirmation",
          f"与既有行程时段冲突 → 转人工确认(实为 {r3['status']})")

    print("\n【5】目标 Agent 不存在 → 必须转人工(不得默认放行)")
    r4 = os_.supervisor.cross_request(
        from_agent="admin", to_agent="不存在的Agent", request_type="budget_request",
        payload={"amount": 1, "category": "travel", "entity": "x", "account": "default"},
        reason="路由到不存在的目标")
    print(f"  申请 → 状态【{r4['status']}】")
    print(f"  裁决: {r4['verdict']}")
    check(r4["status"] == "needs_confirmation",
          f"目标不存在 → needs_confirmation 而非默认放行(实为 {r4['status']})")

    import shutil
    shutil.rmtree(_data, ignore_errors=True)

    print("\n" + "=" * 60)
    print("✓ 主管编排验证完成:全局态势 + 跨 Agent 守门人协调成立")
    print("=" * 60)


def test_orchestration():
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
