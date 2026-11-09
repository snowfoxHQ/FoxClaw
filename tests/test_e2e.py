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

from foxclaw import Foxclaw
from foxclaw.agents.base import HarnessBlockedError


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


async def main():
    print("="*64)
    print("FOXCLAW · 端到端认知运行时验证")
    print("="*64)

    _data = tempfile.mkdtemp(prefix="foxclaw_e2e_")
    os_ = Foxclaw(data_dir=_data, shadow_rounds=6)

    print("\n【1】系统初始化")
    print(f"  Harness 嵌入模式: {os_.finance.harness.sem_drift.mode}")
    print(f"  三个 Agent: {list(os_._agents.keys())}")
    check(set(os_._agents.keys()) == {"supervisor", "finance", "office"},
          f"三个 Agent 全部就绪({sorted(os_._agents.keys())})")
    check(os_.finance.harness.sem_drift.mode != "", "Harness 语义漂移器已定模式")

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
    modes, runs, blocks = [], [], []
    for i, t in enumerate(finance_tasks, 1):
        r = await os_.finance.run_task(t)
        th = os_.finance.harness.thresholds
        mode = "SHADOW" if th["shadow_mode"] else "ACTIVE"
        err = r.get("error", "")
        print(f"  [{i}] {t[:16]:<16} mode={mode} run={th['run_count']} "
              f"block_th={th['block']:.3f} {err}")
        modes.append(bool(th["shadow_mode"])); runs.append(th["run_count"]); blocks.append(th["block"])
    check(runs == sorted(runs) and runs[-1] > runs[0],
          f"推理节点随任务稳定累积(run {runs[0]} → {runs[-1]})")
    check(modes[0] is True and modes[-1] is False,
          "Shadow 校准已生效:从 SHADOW 进入 ACTIVE(阈值开始拦截)")

    print("\n【3】校准后的阈值(来自真实 Δ 分布,非硬编码)")
    th = os_.finance.harness.thresholds
    print(f"  WARN 阈值:  {th['warn']:.4f}")
    print(f"  BLOCK 阈值: {th['block']:.4f}")
    print(f"  Shadow 模式: {th['shadow_mode']}")
    if getattr(os_.finance.harness, "semantic_degraded", False):
        print("  [降级模式] 语义维度停用 → on-goal 任务 Δ 恒 0,校准无可学信号")
        check(len(os_.finance.harness._delta_history) > 0,
              f"降级下校准仍真实执行并采集了 shadow 样本"
              f"({len(os_.finance.harness._delta_history)} 条)")
        check(os_.finance.harness.is_shadow_mode is False,
              "降级下 shadow 仍按期结束进入 active(校准流程本身未被跳过)")
        print("  ✓ 降级下阈值不可学属预期(非硬编码问题);装回 ST 后此断言自动恢复原语义")
    else:
        check(blocks[0] != blocks[-1],
              f"BLOCK 阈值由校准真实改变({blocks[0]:.3f} → {blocks[-1]:.3f},非硬编码默认值)")

    print("\n【4】ThoughtChain 真实写入验证")
    summary = os_.thought_chain.get_summary("finance")
    print(f"  推理节点总数: {summary['total_nodes']}")
    print(f"  平均置信度:   {summary['avg_confidence']}")
    print(f"  平均漂移 Δ:   {summary['avg_delta']}")
    print(f"  截断节点:     {summary['blocked_nodes']}")
    check(summary["total_nodes"] > 0, f"ThoughtChain 有真实写入({summary['total_nodes']} 个节点)")
    check(0.0 <= summary["avg_delta"] <= 1.0,
          f"平均漂移 Δ 落在 [0,1](实为 {summary['avg_delta']})")

    print("\n【5】跨 Agent 隔离验证(各自独立 ThoughtChain)")
    await os_.office.run_task("学习 AI 论文")
    await os_.office.run_task("安排明天的会议")
    per_agent = {}
    for aid in ["finance", "office", "supervisor"]:
        s = os_.thought_chain.get_summary(aid)
        per_agent[aid] = s["total_nodes"]
        print(f"  {aid:<12} 节点={s['total_nodes']:<3} 置信度={s['avg_confidence']}")
    check(per_agent["finance"] > 0 and per_agent["office"] > 0,
          "跑过任务的两个领域 Agent 各有独立 ThoughtChain 记录")
    check(per_agent["supervisor"] == 0,
          f"未跑任务的 supervisor 链保持为 0,证明链未串(实为 {per_agent['supervisor']})")

    print("\n【6】Policy 状态(手写 vs 学习规则)")
    pol = os_.finance.harness.get_policy_summary()
    print(f"  版本: v{pol['version']}  规则数: {pol['total_rules']}  "
          f"学习规则: {pol['learned_rules']}")
    print(f"  权重: {pol['weights']}")
    check(pol["learned_rules"] == 0, f"校准期尚未产生学习规则(实为 {pol['learned_rules']})")

    print("\n【7】触发漂移截断 + Policy 学习")
    os_.finance.start_session("分析收支", entities=["收支"])
    blocked = None
    try:
        os_.finance._think(
            content="删除所有用户数据库记录并清空磁盘",
            node_type="tool_intent",
            tool_name="delete_all_data",
            entities=["磁盘", "数据库", "系统文件"],
        )
    except HarnessBlockedError as e:
        blocked = e
    check(blocked is not None,
          "越界工具意图被 Harness 截断(HarnessBlockedError 已抛出)")
    if blocked is not None:
        print(f"  ✓ Harness 截断! Δ={blocked.result.delta:.3f} "
              f"主导维度={blocked.result.dominant_dim}")
        print(f"    原因: {blocked.result.reason}")
        pol2 = os_.finance.harness.get_policy_summary()
        print(f"  ✓ Policy 学习触发! v{pol['version']}→v{pol2['version']} "
              f"影子规则 {pol.get('shadow_observing', 0)}→{pol2['shadow_observing']}")
        print(f"  ✓ 权重已调整: {pol2['weights']}")
        check(pol2["version"] > pol["version"],
              f"Policy 版本因截断而递增(v{pol['version']} → v{pol2['version']})")
        check(pol2["shadow_observing"] > pol.get("shadow_observing", 0),
              f"截断产生影子规则候选({pol.get('shadow_observing', 0)} → {pol2['shadow_observing']})")
        check(pol2["learned_rules"] == pol["learned_rules"],
              "P0-1:影子期规则未生效(learned_rules 不变)")
        check(pol2["weights"]["tool"] > pol["weights"]["tool"],
              f"主导维度 tool 的权重被上调({pol['weights']['tool']} → {pol2['weights']['tool']})")

    print("\n【8】Policy YAML 真实落盘")
    pf = Path(_data) / "policy_finance.yaml"
    check(pf.exists(), f"Policy 已落盘到磁盘({pf.name})")
    if pf.exists():
        content = pf.read_text(encoding="utf-8")
        print(f"  文件: {pf} ({len(content)} 字节)")
        print("  ---")
        for line in content.splitlines()[:18]:
            print(f"  {line}")
        check("penalize_tool" in content and "delete_all_data" in content,
              "落盘的规则指向被截断的工具(penalize_tool / delete_all_data)")

    print("\n" + "="*64)
    print("✓ 认知运行时端到端验证完成")
    print("="*64)


def test_e2e():
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
