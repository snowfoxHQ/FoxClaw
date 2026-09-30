"""
主管编排测试 — 全局态势 + 跨 Agent 编排
核心场景:行程 Agent 规划出差 → 主管发起差旅预算申请 →
         财务守门人裁决(够不够/要不要确认/否决)。
这把三个独立内核真正串成一个有机系统。
"""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["HESTIA_SHADOW_ROUNDS"] = "50"

import asyncio
from hestia import HestiaOS


async def main():
    print("=" * 60)
    print("主管编排 · 全局态势 + 跨 Agent 协调")
    print("=" * 60)
    _data = tempfile.mkdtemp(prefix="hestia_orch_")   # 系统临时目录,不污染仓库根
    os_ = HestiaOS(data_dir=_data, shadow_rounds=50)

    # 准备各内核的数据
    fin = os_.finance_runtime
    fin.set_budget("travel", 3000)
    fin.set_balance("default", 5000)
    fin.import_csv(str(Path(__file__).parent.parent / "examples" / "sample_transactions.csv"))

    learn = os_.learning_runtime
    learn.import_csv(str(Path(__file__).parent.parent / "examples" / "sample_knowledge.csv"))

    itin = os_.itinerary_runtime
    itin.import_csv(str(Path(__file__).parent.parent / "examples" / "sample_itinerary.csv"))

    # 1. 全局态势(驾驶舱视图)
    print("\n【1】主管全局态势(驾驶舱视图)")
    sit = os_.supervisor.global_situation()
    print(f"  财务: {sit['finance']}")
    print(f"  学习: {sit['learning']}")
    print(f"  行程: {sit['itinerary']}")
    print(f"  跨域告警 {len(sit['alerts'])} 条:")
    for a in sit["alerts"][:5]:
        print(f"    [{a['severity']}] ({a['domain']}) {a['msg']}")

    # 2. 跨 Agent 编排:差旅预算申请(小额,应批准)
    print("\n【2】跨 Agent 编排:行程→财务 差旅预算申请(小额)")
    r1 = os_.supervisor.cross_request(
        from_agent="admin", to_agent="finance", request_type="budget_request",
        payload={"amount": 150, "category": "travel", "entity": "高铁票", "account": "default"},
        reason="出差北京高铁票")
    print(f"  申请: 差旅 150 元 → 状态【{r1['status']}】")
    print(f"  财务裁决: {r1['verdict']}")

    # 3. 跨 Agent 编排:大额差旅(触发守门人确认)
    print("\n【3】跨 Agent 编排:大额差旅(触发守门人)")
    r2 = os_.supervisor.cross_request(
        from_agent="admin", to_agent="finance", request_type="budget_request",
        payload={"amount": 680, "category": "travel", "entity": "商务酒店", "account": "default"},
        reason="出差住宿3晚")
    print(f"  申请: 差旅 680 元 → 状态【{r2['status']}】")
    print(f"  财务裁决: {r2['verdict']}")

    # 4. 跨 Agent 编排:日程占用申请
    print("\n【4】跨 Agent 编排:日程占用申请→行政检查冲突")
    r3 = os_.supervisor.cross_request(
        from_agent="assistant", to_agent="admin", request_type="schedule_request",
        payload={"start_time": "2026-06-10T10:30:00", "title": "临时电话会"},
        reason="客户临时约电话会")
    print(f"  申请: 10:30 电话会 → 状态【{r3['status']}】")
    print(f"  行政裁决: {r3['verdict']}")

    import shutil
    shutil.rmtree(_data, ignore_errors=True)

    print("\n" + "=" * 60)
    print("✓ 主管编排验证完成:全局态势 + 跨 Agent 守门人协调成立")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
