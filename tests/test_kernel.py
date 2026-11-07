"""
内核新模块回归测试 — 覆盖 Codex 融合 + AHS 落地的全部新能力

验证:
  1. 认知调度器(意图/优先级/负载/抢占)
  2. 进程状态机(生命周期/非法流转拦截/幂等)
  3. 系统事件总线(发布订阅/历史)
  4. 真值管线(执行→审计→记忆事件链)
  5. 认知状态快照(聚合/去重/None防护)
  6. 权限管线(四阶段 deny>ask>allow)
  7. 断路器(连续失败开闸)+ 钩子
  8. AHS 执行链(router→planner→composition 端到端)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


def test_scheduler():
    print("\n【1】认知调度器")
    from foxclaw.core.scheduler import CognitiveScheduler, SystemContext
    sch = CognitiveScheduler()
    d_reason = sch.schedule("帮我规划设计方案")
    d_tool = sch.schedule("算一下 5+5")
    check(d_reason.intent.category == "reasoning", "推理类意图识别")
    check(d_tool.intent.category == "tool", "工具类意图识别")
    check(d_reason.priority > d_tool.priority, "推理类优先级 > 工具类")
    idle = sch.schedule("规划项目", SystemContext(active_agents=0, system_load=0.1))
    busy = sch.schedule("规划项目", SystemContext(active_agents=4, queue_depth=3, system_load=0.8))
    check(idle.priority > busy.priority, "高负载下优先级被压低")
    preempt = sch.schedule("紧急规划重大项目", SystemContext(active_agents=3))
    check(preempt.preemptible, "高优先级+Agent压力触发抢占")


def test_process_manager():
    print("\n【2】进程状态机")
    from foxclaw.kernel import ProcessManager
    pm = ProcessManager()
    p = pm.create("任务", agent_id="finance", schedule={"priority": 0.5})
    check(p.state == "scheduled", "创建后进入 scheduled")
    check(pm.active_count() == 1, "活跃计数=1(create 后)")
    pm.mark_running(p.process_id)
    check(pm.active_count() == 1, "running 仍算活跃")
    pm.mark_completed(p.process_id)
    check(pm.active_count() == 0, "completed 后活跃计数归零(O(1) 增量)")
    check(pm.get(p.process_id).state == "completed", "完整生命周期到 completed")
    check(pm.get(p.process_id).state_history == ["created", "queued", "scheduled", "running", "completed"],
          "状态历史完整")
    # 幂等:已终态再标记不崩溃,且**不重复扣减**活跃计数
    r = pm.mark_failed(p.process_id, "二次")
    check(r is not None and r.state == "completed", "终态重复标记幂等不崩溃")
    check(pm.active_count() == 0, "终态重复标记不重复扣减活跃计数(仍为0,不为负)")
    # 非法流转拦截
    p2 = pm.create("x", schedule={"priority": 0.1})
    p2.state = "created"
    try:
        pm._transition(p2, "completed")
        check(False, "非法流转应被拦")
    except ValueError:
        check(True, "非法流转(created→completed)被拦截")


def test_system_bus():
    print("\n【3】系统事件总线")
    from foxclaw.core.system_bus import SystemBus
    bus = SystemBus()
    received = []
    bus.subscribe("test.ch", lambda m: received.append(m.payload))
    bus.publish("test.ch", {"x": 1})
    check(received == [{"x": 1}], "发布订阅传递正确")
    check(len(bus.history("test.ch")) == 1, "事件历史记录")


def test_truth_pipeline():
    print("\n【4】真值管线")
    from foxclaw.core.system_bus import SystemBus
    from foxclaw.core.truth_pipeline import TruthPipeline

    class FakeHMR:
        def __init__(self): self.items = []
        def ingest(self, content, **kw): self.items.append(content)

    bus = SystemBus()
    hmr = FakeHMR()
    tp = TruthPipeline(bus, hmr=hmr)
    # 【P3-9】TruthPipeline.emit_execution 已删除:事件名即契约,直接经 bus 入队。
    bus.publish("execution.completed", {"agent_id": "finance", "task": "分析预算",
                                        "result": "预算正常", "blocked": False})
    check(tp.last_trace is not None, "执行→审计 trace 生成")
    check(len(hmr.items) == 1, "真值沉淀到记忆")
    bus.publish("execution.completed", {"agent_id": "finance", "task": "危险",
                                        "result": "x", "blocked": True})
    check(not tp.last_memory_update.get("persisted"), "被拦截任务不沉淀记忆")


def test_cognitive_state():
    print("\n【5】认知状态快照")
    from foxclaw.core.system_bus import SystemBus
    from foxclaw.cognitive import CognitiveStateTracker
    bus = SystemBus()
    t = CognitiveStateTracker(bus)
    bus.publish("execution.completed", {"task": "分析财务", "agent_id": "finance",
                "schedule": {"intent": "reasoning", "route_hint": "agent", "priority": 0.4}})
    s = t.snapshot()
    check(s["active_goal"] == "分析财务", "活跃目标聚合")
    check(s["semantic_focus"] == "reasoning", "语义焦点聚合")
    # 去重
    for _ in range(5):
        bus.publish("memory.updated", {"memory_update": {"content": "同样"}})
    check(len(t.snapshot()["activated_memories"]) == 1, "重复记忆去重")
    # None 防护
    class M: payload = None
    t._on_exec(M())
    check(True, "None payload 不崩溃")


def test_permission():
    print("\n【6】权限管线")
    from foxclaw.skills import build_default_registry, SkillAdapter
    from foxclaw.skills.permission import PermissionRules
    reg = build_default_registry()
    ad = SkillAdapter(reg)
    calc = reg.get("calculator")
    r = ad.invoke(calc, {"expression": "1+1"})
    check(r.success, "只读 skill 放行")
    gmail = reg.get("gmail_send")
    r2 = ad.invoke(gmail, {"to": "x", "subject": "y", "body": "z"})
    check(r2.status == "needs_confirmation", "发邮件未确认被拦")
    rules = PermissionRules(deny=["gmail_send"])
    ad2 = SkillAdapter(reg, rules=rules)
    r3 = ad2.invoke(gmail, {"to": "x", "subject": "y", "body": "z"}, confirmed=True)
    check(r3.status == "blocked", "deny 铁律压倒已确认")


def test_trust_single_source():
    """【P6】信任分级单一真源:permission 复用 SkillSpec.trust_level(),不再有第二套 dict。
    此前 permission.TRUST_LEVEL 与 trust_level() 重复(且含死 key bundled/remote)——对 3 个合法
    SkillKind 二者 downgrade 行为本就一致(latent,非 live bug),本次收敛为单一真源、行为不变。"""
    print("\n【6b】信任分级单一真源(P6):permission 复用 trust_level(),删死字典")
    from foxclaw.skills import SkillSpec
    from foxclaw.skills import permission as perm
    from foxclaw.skills.permission import evaluate
    check(not hasattr(perm, "TRUST_LEVEL"), "permission.TRUST_LEVEL 死字典已删(不再双真源)")
    # 3 个合法 kind × 两种 source:permission 的 downgrade 恒等于 (trust_level()=='low'),且行为不变
    expect = {"local": False, "web_api": False, "mcp": True}   # behavior-preserving 基线
    for kind, want_dg in expect.items():
        for source in ("builtin", "user"):
            s = SkillSpec(id=f"s_{kind}_{source}", name="t", description="d",
                          kind=kind, side_effect="read", source=source)
            d = evaluate(s, {})
            check(d.trust_downgrade == (s.trust_level() == "low"),
                  f"{kind}/{source}: downgrade 严格跟随 trust_level()(单一真源)")
            check(d.trust_downgrade == want_dg,
                  f"{kind}: downgrade={want_dg}(重构前后一致)")
    print("    结论:全部合法 kind 行为不变 + 双真源分歧点已消除")


def test_breaker_hooks():
    print("\n【7】断路器 + 钩子")
    from foxclaw.skills import SkillRegistry, SkillSpec, SkillAdapter
    from foxclaw.skills.lifecycle import CircuitBreaker, HookManager
    reg = SkillRegistry()
    reg.register(SkillSpec(id="flaky", name="易碎", description="总失败",
                 kind="local", side_effect="read", handler=lambda **k: (_ for _ in ()).throw(RuntimeError("boom"))))
    ad = SkillAdapter(reg, breaker=CircuitBreaker(threshold=3))
    for _ in range(3):
        ad.invoke(reg.get("flaky"), {})
    check(ad.breaker.status("flaky")["state"] == "open", "连续失败3次断路器开闸")
    r = ad.invoke(reg.get("flaky"), {})
    check(r.status == "circuit_open", "开闸后停止调用")
    # 钩子
    log = []
    hm = HookManager()
    hm.register("pre_skill", lambda ctx: log.append("pre"))
    hm.register("post_skill", lambda ctx: log.append("post"))
    reg.register(SkillSpec(id="good", name="好", description="成功",
                 kind="local", side_effect="read", handler=lambda **k: {"ok": 1}))
    ad2 = SkillAdapter(reg, hooks=hm)
    ad2.invoke(reg.get("good"), {})
    check(log == ["pre", "post"], "钩子 pre→post 顺序")


def test_ahs_pipeline():
    print("\n【8】AHS 执行链")
    from foxclaw.skills import build_default_registry, SkillAdapter
    from foxclaw.skills.pipeline import AHSPipeline, route, plan
    reg = build_default_registry()
    ad = SkillAdapter(reg)
    pipe = AHSPipeline(reg, ad)
    # router 否定词
    r = route("不要计算", reg)
    check(r.primary != "calculator", "否定词检测:不要计算→不选calculator")
    # planner 拆解
    p = plan("先算 5+5 然后再算 9*9", reg)
    check(len(p.tasks) == 2, "planner 多步拆解")
    # 端到端
    res = pipe.run("帮我算一下 12*12")
    check(res.success, "AHS 执行链端到端成功")


def main():
    print("=" * 60)
    print("内核新模块回归测试")
    print("=" * 60)
    tests = [test_scheduler, test_process_manager, test_system_bus,
             test_truth_pipeline, test_cognitive_state, test_permission,
             test_breaker_hooks, test_ahs_pipeline, test_trust_single_source]
    for t in tests:
        t()
    print("\n" + "=" * 60)
    print("✓ 内核新模块全部通过")
    print("=" * 60)


if __name__ == "__main__":
    main()
