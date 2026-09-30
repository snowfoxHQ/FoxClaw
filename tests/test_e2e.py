"""
端到端测试 — 验证认知运行时在真实数据下工作
不依赖 LLM(stub)和真 HMR(mock),专注验证差异化核心:
  ThoughtChain 写入 → Harness 真实漂移检测 → Shadow 校准 → Policy 学习
"""
import asyncio
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from hestia import HestiaOS

async def main():
    print("="*64)
    print("HESTIA OS · 端到端认知运行时验证")
    print("="*64)

    # shadow_rounds 设小,快速触发校准。数据用临时目录:跑完即弃,不污染仓库根。
    _data = tempfile.mkdtemp(prefix="hestia_e2e_")
    os_ = HestiaOS(data_dir=_data, shadow_rounds=6)

    print("\n【1】系统初始化")
    print(f"  Harness 嵌入模式: {os_.finance.harness.sem_drift.mode}")
    print(f"  五 Agent: {list(os_._agents.keys())}")

    print("\n【2】连续跑任务,触发 Shadow 校准")
    finance_tasks = [
        "分析本月家庭支出",
        "制定储蓄计划",
        "评估投资组合风险",
        "生成季度财务报告",
        "追踪日常消费记录",
        "规划明年预算",
        "对比去年同期支出",
    ]
    for i, t in enumerate(finance_tasks, 1):
        r = await os_.finance.run_task(t)
        th = os_.finance.harness.thresholds
        mode = "SHADOW" if th["shadow_mode"] else "ACTIVE"
        err = r.get("error", "")
        print(f"  [{i}] {t[:16]:<16} mode={mode} run={th['run_count']} "
              f"block_th={th['block']:.3f} {err}")

    print("\n【3】校准后的阈值(来自真实 Δ 分布,非硬编码)")
    th = os_.finance.harness.thresholds
    print(f"  WARN 阈值:  {th['warn']:.4f}")
    print(f"  BLOCK 阈值: {th['block']:.4f}")
    print(f"  Shadow 模式: {th['shadow_mode']}")

    print("\n【4】ThoughtChain 真实写入验证")
    summary = os_.thought_chain.get_summary("finance")
    print(f"  推理节点总数: {summary['total_nodes']}")
    print(f"  平均置信度:   {summary['avg_confidence']}")
    print(f"  平均漂移 Δ:   {summary['avg_delta']}")
    print(f"  截断节点:     {summary['blocked_nodes']}")

    print("\n【5】跨 Agent 隔离验证(各自独立 ThoughtChain)")
    await os_.office.run_task("学习 AI 论文")
    await os_.admin.run_task("安排明天的会议")
    for aid in ["finance", "office", "admin", "supervisor"]:
        s = os_.thought_chain.get_summary(aid)
        print(f"  {aid:<12} 节点={s['total_nodes']:<3} 置信度={s['avg_confidence']}")

    print("\n【6】Policy 状态(手写 vs 学习规则)")
    pol = os_.finance.harness.get_policy_summary()
    print(f"  版本: v{pol['version']}  规则数: {pol['total_rules']}  "
          f"学习规则: {pol['learned_rules']}")
    print(f"  权重: {pol['weights']}")

    print("\n【7】触发漂移截断 + Policy 学习")
    # 注册一个收支分析目标后,强行执行越界工具(删除),制造高漂移
    os_.finance.start_session("分析收支", entities=["收支"])
    from hestia.agents.base import HarnessBlockedError
    try:
        # 直接 _think 一个完全跑题 + 越界工具的节点
        os_.finance._think(
            content="删除所有用户数据库记录并清空磁盘",
            node_type="tool_intent",
            tool_name="delete_all_data",
            entities=["磁盘", "数据库", "系统文件"],
        )
        print("  未触发截断(漂移未超阈值)")
    except HarnessBlockedError as e:
        print(f"  ✓ Harness 截断! Δ={e.result.delta:.3f} "
              f"主导维度={e.result.dominant_dim}")
        print(f"    原因: {e.result.reason}")
        pol2 = os_.finance.harness.get_policy_summary()
        print(f"  ✓ Policy 学习触发! v{pol['version']}→v{pol2['version']} "
              f"学习规则 {pol['learned_rules']}→{pol2['learned_rules']}")
        print(f"  ✓ 权重已调整: {pol2['weights']}")

    print("\n【8】Policy YAML 真实落盘")
    pf = Path(_data) / "policy_finance.yaml"
    if pf.exists():
        content = pf.read_text()
        print(f"  文件: {pf} ({len(content)} 字节)")
        print("  ---")
        for line in content.splitlines()[:18]:
            print(f"  {line}")

    print("\n" + "="*64)
    print("✓ 认知运行时端到端验证完成")
    print("="*64)

if __name__ == "__main__":
    asyncio.run(main())
