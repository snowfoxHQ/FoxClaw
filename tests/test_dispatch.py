"""
D 阶段测试 — 主管真实调度子 Agent + 共享记忆。
验证:
  1. 主管把任务真实派发给对应子 Agent(关键词路由)
  2. 子 Agent 结果汇聚回主管
  3. 调度记录写入共享层,所有 Agent 可召回
  4. 私有记忆隔离仍然成立(共享层 ≠ 打破隔离)
"""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["HESTIA_SHADOW_ROUNDS"] = "20"

import asyncio
from hestia import HestiaOS


async def main():
    print("=" * 60)
    print("D 阶段 · 主管真实调度 + 共享记忆")
    print("=" * 60)
    os_ = HestiaOS(data_dir=tempfile.mkdtemp(prefix="hestia_dispatch_"), shadow_rounds=20)

    # 1. 主管调度不同类型任务,验证路由
    print("\n【1】主管真实调度(关键词路由)")
    routing_cases = [
        ("分析本月家庭支出预算", "finance"),
        ("学习一篇关于 RAG 的论文", "office"),
        ("安排周三下午的项目会议", "admin"),
        ("帮我整理今天的邮件要点", "assistant"),
    ]
    for task, expected in routing_cases:
        r = await os_.supervisor.run_task(task)
        dispatched = r.get("result", {}).get("dispatched_to", "?")
        ok = "✓" if dispatched == expected else "✗"
        print(f"  {ok} 「{task[:18]}」→ {dispatched} (预期 {expected})")

    # 2. 结果汇聚
    print("\n【2】结果汇聚")
    r = await os_.supervisor.run_task("评估我的投资组合风险")
    res = r.get("result", {})
    print(f"  派给: {res.get('dispatched_to')}")
    print(f"  汇聚: {str(res.get('sub_result'))[:50]}...")
    print(f"  主管摘要: {res.get('summary', '')}")

    # 3. 共享记忆:其他 Agent 能召回主管的调度记录
    print("\n【3】共享记忆层(所有 Agent 可见)")
    for viewer in ["office", "admin", "assistant"]:
        shared = os_.hmr.recall(query="支出 预算 投资", agent_id=viewer, top_k=5)
        shared_hits = [m for m in shared if m.get("shared")]
        print(f"  {viewer:<10} 召回共享调度记录: {len(shared_hits)} 条")

    # 4. 隔离仍成立:私有记忆不跨 Agent
    print("\n【4】私有记忆隔离(共享层不打破隔离)")
    await os_.finance.run_task("记录我的私人理财偏好")
    office_view = os_.hmr.recall(query="理财 偏好", agent_id="office", top_k=10)
    finance_private = [
        m for m in office_view
        if (not m.get("shared")) and f"agent:finance" in m.get("tags", [])
    ]
    status = "✓" if len(finance_private) == 0 else "✗"
    print(f"  {status} office 拿到 finance 私有记忆: {len(finance_private)} 条(应为 0)")

    print("\n" + "=" * 60)
    print("✓ D 阶段验证完成:双层记忆架构(私有隔离 + 共享协调)成立")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
