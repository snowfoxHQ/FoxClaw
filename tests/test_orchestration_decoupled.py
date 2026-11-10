"""
编排层去领域硬编码(P3)测试 —— 全离线。

审计:Orchestrator 的 _handle_budget_request/_handle_schedule_request + gather_situation
硬编码了 finance/learning/itinerary 内核内部 API(SRP 违反,编排层懂领域)。
修复:领域知识下沉到各领域 Agent(situation()/handle_cross_request()),编排层只做
通用聚合(逐个 agent.situation())+ 通用路由(dispatch 到 target.handle_cross_request())。

覆盖:
  1. 结构去耦:Orchestrator 删除 _handle_* 领域方法;编排层源码零领域内核 API 引用
  2. 领域逻辑归属 Agent:finance/admin 有 handle_cross_request,base 默认放行
  3. 行为保持:态势聚合 + 小额批准/大额确认/日程冲突确认(裁决与原一致)
  4. 通用性:路由到不存在的 Agent → 转人工确认(P1-0 修 fail-open,仍不崩)
"""
import os, sys, asyncio, tempfile, shutil
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_EMBEDDING_MODEL"] = "foxclaw-nonexistent-fast"
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_structural_decoupling():
    print("\n【1】结构去耦:编排层无领域方法/无领域内核 API 引用")
    from foxclaw.orchestration import Orchestrator
    assert not hasattr(Orchestrator, "_handle_budget_request"), "编排层不应再有 _handle_budget_request"
    assert not hasattr(Orchestrator, "_handle_schedule_request"), "编排层不应再有 _handle_schedule_request"
    src = Path("foxclaw/orchestration/__init__.py").read_text(encoding="utf-8")
    for banned in ("budget_status", "detect_conflicts", "due_items", "Transaction",
                   ".waypoints", ".policy", "review_states"):
        assert banned not in src, f"编排层仍引用领域内核 API: {banned}"
    print("  ✓ 删除 _handle_*;源码零领域内核 API 引用")


def test_domain_logic_in_agents():
    print("\n【2】领域逻辑归属 Agent(situation / handle_cross_request)")
    from foxclaw.agents.agents import FinanceAgent, OfficeAgent
    from foxclaw.agents.base import CognitiveAgent
    assert FinanceAgent.handle_cross_request is not CognitiveAgent.handle_cross_request
    assert OfficeAgent.handle_cross_request is not CognitiveAgent.handle_cross_request
    assert FinanceAgent.situation is not CognitiveAgent.situation
    assert OfficeAgent.situation is not CognitiveAgent.situation
    print("  ✓ finance/admin 覆盖 handle_cross_request;finance/admin/office 覆盖 situation")


def test_behavior_and_generic_routing():
    print("\n【3】行为保持 + 通用路由")
    d = tempfile.mkdtemp(prefix="p3_")
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        fin = os_.finance_runtime
        fin.set_budget("travel", 3000); fin.set_balance("default", 5000)
        fin.import_csv(str(Path("examples/sample_transactions.csv")))
        os_.learning_runtime.import_csv(str(Path("examples/sample_knowledge.csv")))
        os_.itinerary_runtime.import_csv(str(Path("examples/sample_itinerary.csv")))

        sit = os_.supervisor.global_situation()
        assert set(sit["finance"]) >= {"transactions"} and "total_items" in sit["learning"]
        assert "waypoints" in sit["itinerary"]
        print(f"  ✓ 态势聚合: fin={sit['finance']}, learn.items={sit['learning']['total_items']}, "
              f"itin.wp={sit['itinerary']['waypoints']}, alerts={len(sit['alerts'])}")

        r_small = os_.supervisor.cross_request("admin", "finance", "budget_request",
            {"amount": 150, "category": "travel", "entity": "高铁", "account": "default"}, "小额")
        r_big = os_.supervisor.cross_request("admin", "finance", "budget_request",
            {"amount": 680, "category": "travel", "entity": "酒店", "account": "default"}, "大额")
        r_sched = os_.supervisor.cross_request("assistant", "admin", "schedule_request",
            {"start_time": "2026-06-10T10:30:00", "title": "电话会"}, "占日程")
        assert r_small["status"] == "approved", r_small
        assert r_big["status"] in ("needs_confirmation", "rejected"), r_big
        assert r_sched["status"] in ("needs_confirmation", "approved"), r_sched
        print(f"  ✓ 小额={r_small['status']} 大额={r_big['status']} 日程={r_sched['status']}(委托目标守门人)")

        r_unknown = os_.supervisor.cross_request("x", "nonexistent", "budget_request", {}, "未知目标")
        assert r_unknown["status"] == "needs_confirmation", r_unknown
        assert "不存在" in r_unknown["verdict"]
        print(f"  ✓ 路由到不存在 Agent → {r_unknown['status']}(不再 fail-open 放行,仍不崩)")

        r_bad = os_.supervisor.cross_request("x", "admin", "schedule_request",
            {"start_time": "不是合法时间"}, "畸形时间")
        assert r_bad["status"] == "needs_confirmation", f"裁决崩溃应 fail-safe,实得 {r_bad}"
        assert "转人工" in r_bad["verdict"]
        print(f"  ✓ 畸形 payload 让守门人裁决崩溃 → {r_bad['status']}(fail-safe,不 fail-open 放行)")
        asyncio.run(os_.aclose())
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    print("=" * 60)
    print("编排层去领域硬编码 · 改委托(P3)")
    print("=" * 60)
    test_structural_decoupling()
    test_domain_logic_in_agents()
    test_behavior_and_generic_routing()
    print("\n" + "=" * 60)
    print("✓ P3 验证完成:编排层通用聚合/路由,领域知识归属领域 Agent")
    print("=" * 60)


if __name__ == "__main__":
    main()
