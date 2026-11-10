"""
认知管线(Cognitive Pipeline, v2 ② Step1 抽壳)测试 —— 全离线,不建整机。

覆盖:
  1. 默认管线 recall→act:stage 按序执行,ctx 逐步填充(memories→result/extra),trace 记录顺序
  2. 同步/异步 stage 混跑
  3. 可插桩:往 stages 里插入一个自定义 stage,按位置执行(为后续 verify/reflect/plan 铺路)
  4. 异常透传:stage 抛异常向上传播(由 run_task 统一兜底,管线本身不吞)
"""
import sys, asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.pipeline import (
    PipelineContext, CognitivePipeline, DEFAULT_STAGES,
    stage_recall, stage_act, stage_verify)


class _Node:
    def __init__(self, status, delta): self.harness_status = status; self.harness_delta = delta


class _FakeChain:
    def __init__(self, nodes): self._nodes = nodes
    def get_chain(self, session_id, agent_id=None): return self._nodes


class FakeAgent:
    """最小假 Agent:只提供管线 stage 需要的钩子。"""
    AGENT_ID = "fake"
    _recall_topk = 5
    def __init__(self, nodes=None, fail_on=None):
        self.thought_chain = _FakeChain(nodes or [])
        self.exec_count = 0
        self._fail_on = fail_on
    def _recall_query(self, task): return f"q:{task}"
    def _recall_memory(self, query, top_k=5):
        self.last_recall = (query, top_k)
        return [{"content": f"mem for {query}"}]
    async def _execute(self, task, context, session_id, memories):
        self.exec_count += 1
        if self._fail_on and self.exec_count == self._fail_on:
            raise RuntimeError("重试期间出错")
        return f"done:{task}#{self.exec_count}", {"call": self.exec_count}


def test_default_pipeline():
    print("\n【1】默认管线 recall→act→verify:按序 + ctx 流动 + trace")
    a = FakeAgent()
    ctx = PipelineContext(agent=a, task="T", session_id="s1")
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(ctx))
    assert a.last_recall == ("q:T", 5), "recall stage 用 _recall_query/_recall_topk"
    assert ctx.memories and ctx.memories[0]["content"] == "mem for q:T"
    assert ctx.result == "done:T#1" and ctx.extra == {"call": 1}
    assert ctx.trace == ["perceive", "recall", "plan", "route", "act", "verify", "reflect"], \
        f"stage 顺序,实得 {ctx.trace}"
    assert ctx.perceived.get("task_len") == 1, "perceive 记录输入特征"
    assert ctx.plan.get("multi_step") is False and ctx.route.get("target") == "self"
    assert ctx.verified.get("passed") is True, "verify 汇总校验结果到 ctx.verified"
    assert ctx.reflect["decision"] == "accept" and ctx.reflect["retries"] == 0
    assert a.exec_count == 1, "默认不重试,_execute 只调一次"
    print(f"  ✓ recall→plan→route→act→verify→reflect 按序;plan={ctx.plan['flow']} "
          f"route={ctx.route['target']} reflect={ctx.reflect['decision']}")


def test_plan_route_stage():
    print("\n【1c】plan/route stage:P1-2 引擎分析结构(informational,不改执行)")
    from foxclaw.pipeline import stage_plan, stage_route
    ctx = PipelineContext(agent=FakeAgent(), task="先分析支出 然后 给出建议")
    stage_plan(ctx); stage_route(ctx)
    assert ctx.plan["multi_step"] is True and len(ctx.plan["steps"]) == 2, ctx.plan
    assert ctx.plan["flow"] == "sequential" and ctx.route["target"] == "self"
    ctx2 = PipelineContext(agent=FakeAgent(), task="分析我的开支")
    stage_plan(ctx2)
    assert ctx2.plan["multi_step"] is False and ctx2.plan["steps"] == ["分析我的开支"]
    print(f"  ✓ 多步 plan={ctx.plan['steps']}(flow={ctx.plan['flow']});单步 multi_step=False")


def test_verify_stage():
    print("\n【1b】verify stage:只读推理节点汇总 warns/max_delta(不新增校验)")
    nodes = [_Node("PASS", 0.1), _Node("WARN", 0.42), _Node("PASS", 0.2), _Node("BASELINE", None)]
    a = FakeAgent(nodes=nodes)
    ctx = PipelineContext(agent=a, task="V", session_id="s2")
    stage_verify(ctx)
    assert ctx.verified == {"passed": True, "nodes": 4, "warns": 1,
                            "max_delta": 0.42, "last_delta": 0.2}, ctx.verified
    print(f"  ✓ verified={ctx.verified}(max_delta=峰值0.42;last_delta=最近0.2,供 reflect 早退)")


def test_insertable_stage():
    print("\n【2】可插桩:插入自定义 stage 按位置执行")
    hits = []
    def stage_probe(ctx):
        hits.append(("probe", len(ctx.memories)))
    stage_probe.stage_name = "probe"
    a = FakeAgent()
    ctx = PipelineContext(agent=a, task="X")
    pipe = CognitivePipeline([stage_recall, stage_probe, stage_act])
    asyncio.run(pipe.run(ctx))
    assert hits == [("probe", 1)], "probe 应在 recall(已填 memories)之后运行"
    assert ctx.trace == ["recall", "probe", "act"]
    print(f"  ✓ 插入 stage 生效,trace={ctx.trace}(后续 verify/reflect 即这样插入)")


def test_exception_propagates():
    print("\n【3】异常透传:stage 抛错向上传播(管线不吞)")
    def boom(ctx):
        raise RuntimeError("stage 炸了")
    a = FakeAgent()
    raised = False
    try:
        asyncio.run(CognitivePipeline([boom, stage_act]).run(PipelineContext(agent=a, task="Y")))
    except RuntimeError:
        raised = True
    assert raised, "stage 异常应向上传播,由 run_task 统一兜底"
    print("  ✓ stage 异常透传(HarnessBlockedError 即由此上抛给 run_task 兜底)")


class _CapSpine:
    def __init__(self): self.events = []
    def publish(self, topic, payload=None, source="", **kw): self.events.append(topic)


class _Caps:
    def __init__(self, spine): self._spine = spine
    def get(self, name, default=None): return self._spine if name == "event_spine" else default


def test_stage_events():
    print("\n【6】逐 stage 事件:每个 stage emit agent.cognition.<stage> 到脊柱")
    spine = _CapSpine()
    a = FakeAgent()
    a._caps = _Caps(spine)
    ctx = PipelineContext(agent=a, task="E", session_id="s3")
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(ctx))
    expected = ["agent.cognition." + n for n in
                ["perceive", "recall", "plan", "route", "act", "verify", "reflect"]]
    assert spine.events == expected, f"逐 stage 事件,实得 {spine.events}"
    a2 = FakeAgent()
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(PipelineContext(agent=a2, task="X")))
    print("  ✓ 7 个 agent.cognition.* 事件按序 emit;无中枢时优雅跳过")


def test_route_skill_optin_inert():
    print("\n【7】A·Step5:route 默认自动选 skill(仅只读+高阈值);可关;有副作用的不自动选")
    from foxclaw.pipeline import stage_route
    from foxclaw.skills import build_default_registry
    reg = build_default_registry()

    a = FakeAgent(); a.skill_registry = reg
    ctx = PipelineContext(agent=a, task="帮我计算 (12+8)*5")
    stage_route(ctx)
    assert ctx.route["target"] == "skill" and ctx.route["skill_id"] == "calculator", \
        f"默认应自动选中只读的 calculator: {ctx.route}"

    ctx_off = PipelineContext(agent=a, task="帮我计算 (12+8)*5",
                              context={"_route_skill": False})
    stage_route(ctx_off)
    assert ctx_off.route["target"] == "self", ctx_off.route

    ctx2 = PipelineContext(agent=a, task="帮我计算 (12+8)*5",
                           context={"_route_skill": {"threshold": 0.5}})
    stage_route(ctx2)
    assert ctx2.route["target"] == "skill" and ctx2.route["skill_id"] == "calculator", ctx2.route
    assert ctx2.route["confidence"] >= 0.5, ctx2.route

    a3 = FakeAgent(); a3.skill_registry = reg
    ctx3 = PipelineContext(agent=a3, task="帮我计算 (12+8)*5",
                           context={"_route_skill": {"threshold": 0.5}})
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(ctx3))
    assert ctx3.route["target"] == "skill", "route 已选中 skill"
    assert ctx3.result == "done:帮我计算 (12+8)*5#1" and a3.exec_count == 1, \
        "无 skill_adapter → 回落 _execute(fail-safe)"

    a4 = FakeAgent()
    ctx4 = PipelineContext(agent=a4, task="计算 1+1", context={"_route_skill": True})
    stage_route(ctx4)
    assert ctx4.route["target"] == "self", "无 skill_registry → 回落 self"

    ctx5 = PipelineContext(agent=a, task="帮我计算 1+1",
                           context={"_route_skill": {"threshold": 1.01}})
    stage_route(ctx5)
    assert ctx5.route["target"] == "self", "阈值>1 → 不选,回落 self"
    print("  ✓ 默认关=self;开启(阈值0.5)选中 calculator 但 act 仍 _execute(执行不变);"
          "无registry/高阈值→回落 self")


def test_act_skill_dispatch_step2():
    print("\n【8】A·Step2:act 按 ctx.route 经 PolicyGate dispatch skill(opt-in;非成功 fail-safe 无副作用)")
    from foxclaw.skills import build_default_registry, SkillAdapter
    reg = build_default_registry()
    adapter = SkillAdapter(reg)

    def mk_agent():
        a = FakeAgent(); a.skill_registry = reg; a.skill_adapter = adapter
        return a

    a = mk_agent()
    ctx = PipelineContext(agent=a, task="帮我计算 (12+8)*5",
                          context={"_route_skill": {"threshold": 0.5}, "_skill_dispatch": True})
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(ctx))
    assert ctx.route["target"] == "skill" and ctx.route["skill_id"] == "calculator", ctx.route
    assert ctx.extra.get("via") == "skill", ctx.extra
    assert ctx.result["output"]["result"] == 100, ctx.result
    assert a.exec_count == 0, "skill 执行了 → 领域 _execute(LLM) 不再被调(首个真改执行)"

    a2 = mk_agent()
    ctx2 = PipelineContext(agent=a2, task="帮我发送邮件给客户",
                           context={"_route_skill": {"threshold": 0.5, "read_only": False},
                                    "_skill_dispatch": True})
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(ctx2))
    assert ctx2.route["skill_id"] == "gmail_send", ctx2.route
    assert ctx2.result["status"] == "needs_confirmation" and ctx2.result["pending"] is True, ctx2.result
    assert a2.exec_count == 0, "未确认的危险 skill → 不执行、不回落 LLM(绝不 fail-open)"

    a3 = mk_agent()
    ctx3 = PipelineContext(agent=a3, task="帮我计算 (12+8)*5",
                           context={"_route_skill": {"threshold": 0.5}, "_skill_dispatch": False})
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(ctx3))
    assert ctx3.route["target"] == "skill" and a3.exec_count == 1, "dispatch 关 → 回落 _execute"

    a4 = FakeAgent(); a4.skill_registry = reg
    ctx4 = PipelineContext(agent=a4, task="帮我计算 1+1",
                           context={"_route_skill": {"threshold": 0.5}, "_skill_dispatch": True})
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(ctx4))
    assert a4.exec_count == 1, "无 adapter → 回落 _execute"

    a2b = mk_agent()
    ctx2b = PipelineContext(agent=a2b, task="帮我发送邮件给客户")
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(ctx2b))
    assert ctx2b.route["target"] == "self", f"有副作用的技能不该被自动选中: {ctx2b.route}"
    assert a2b.exec_count == 1, "未自动动手 → 由 LLM 作答"

    class _BoomAdapter:
        def invoke(self, skill, args, confirmed=False, mode="strict"):
            raise RuntimeError("dispatch 内部炸了")
    a5 = FakeAgent(); a5.skill_registry = reg; a5.skill_adapter = _BoomAdapter()
    ctx5 = PipelineContext(agent=a5, task="帮我计算 1+1",
                           context={"_route_skill": {"threshold": 0.5}, "_skill_dispatch": True})
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(ctx5))
    assert ctx5.result["status"] == "error" and "异常" in ctx5.result["message"], ctx5.result
    assert a5.exec_count == 0, "dispatch 异常 → fail-closed 结构化 error,绝不回落 LLM"
    print("  ✓ 开启:calculator 真执行(=100,不调LLM);发邮件未确认→needs_confirmation零副作用;"
          "dispatch关/无adapter→回落_execute;invoke异常→fail-closed error(不崩不回落LLM)")


def test_run_task_confirm_suspend_step3():
    print("\n【9】A·Step3:skill needs_confirmation 提升为 run_task 顶层 status + _confirmed 恢复执行")
    import tempfile, shutil
    from foxclaw.agents.base import CognitiveAgent
    from foxclaw.core.thought_chain import ThoughtChain
    from foxclaw.skills import build_default_registry, SkillAdapter, SkillSpec

    class _FakeHMR:
        def recall(self, query="", agent_id=None, top_k=5, **kw): return []
        def ingest(self, content, **kw): return None
        def get_workspace(self, agent_id):
            return type("W", (), {"active_goal": "", "push_task": lambda s, t: None})()
        def save_workspace(self, agent_id): pass

    class _Mini(CognitiveAgent):
        AGENT_ID = "mini"
        ALLOWED_TOOL_CATEGORIES = {"read", "write", "network"}
        @property
        def _role_prompt(self): return "test"
        async def _execute(self, task, context, session_id, memories):
            return f"llm:{task}", {"via": "llm"}

    d = tempfile.mkdtemp(prefix="step3_")
    try:
        reg = build_default_registry()
        saved = []
        reg.register(SkillSpec(
            id="save_note", name="记笔记", description="保存一条笔记", kind="local",
            triggers=["记一下", "记笔记", "保存笔记"], side_effect="write",
            handler=lambda text="": (saved.append(text) or {"saved": text}),
            params={"text": "str"}))
        adapter = SkillAdapter(reg)
        ag = _Mini(hmr=_FakeHMR(), thought_chain=ThoughtChain(db_path=f"{d}/tc.db"),
                   data_dir=d, shadow_rounds=5)
        ag.skill_registry = reg; ag.skill_adapter = adapter
        base_ctx = {"_route_skill": {"threshold": 0.4, "read_only": False},
                    "_skill_dispatch": True}

        r1 = asyncio.run(ag.run_task("帮我记一下 买牛奶", dict(base_ctx)))
        assert r1.get("status") == "needs_confirmation", r1
        assert r1.get("pending_action", {}).get("skill_id") == "save_note", r1
        assert saved == [], "未确认 → handler 不执行(零副作用)"

        r_bad = asyncio.run(ag.run_task("帮我记一下 买牛奶",
                                        {**base_ctx, "_confirmed": "gmail_send"}))
        assert r_bad.get("status") == "needs_confirmation", r_bad
        assert saved == [], "确认绑定到别的 skill → 不执行本 skill(绑定优先)"

        r2 = asyncio.run(ag.run_task("帮我记一下 买牛奶",
                                     {**base_ctx, "_confirmed": "save_note"}))
        assert r2.get("status") != "needs_confirmation", r2
        assert len(saved) == 1, f"确认后 handler 执行(有副作用),saved={saved}"

        saved.clear()
        r2b = asyncio.run(ag.run_task("帮我记一下 买酸奶", {**base_ctx, "_confirmed": True}))
        assert r2b.get("status") != "needs_confirmation" and len(saved) == 1, (r2b, saved)

        r3 = asyncio.run(ag.run_task("随便聊聊"))
        assert "status" not in r3 and r3["result"] == "llm:随便聊聊", r3
        print(f"  ✓ 未确认→顶层 needs_confirmation 零副作用;_confirmed 重跑→执行(saved={saved});"
              f"LLM 路径无 status(行为不变)")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_reflect_retry_optin():
    print("\n【4】reflect opt-in:漂移超阈值 → 有上限重试")
    from foxclaw.pipeline import stage_verify   # noqa
    a = FakeAgent(nodes=[_Node("WARN", 0.8)])
    ctx = PipelineContext(agent=a, task="V",
                          context={"_reflect": {"max_retries": 2, "drift_threshold": 0.5}})
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(ctx))
    assert a.exec_count == 3, f"1 初次 + 2 重试,实得 {a.exec_count}"
    assert ctx.reflect["decision"] == "retry" and ctx.reflect["retries"] == 2, ctx.reflect
    assert ctx.result == "done:V#3", "最终结果=最后一次重试"
    print(f"  ✓ 重试到上限:exec={a.exec_count} reflect={ctx.reflect}")


class _DynChain:
    """动态 chain:重试后追加一个低漂移新节点(旧高节点仍在)。用于证明 reflect 用 last_delta 早退。"""
    def __init__(self, agent): self.agent = agent
    def get_chain(self, session_id, agent_id=None):
        if self.agent.exec_count <= 1:
            return [_Node("WARN", 0.8)]
        return [_Node("WARN", 0.8), _Node("PASS", 0.2)]


def test_reflect_early_exit_on_improve():
    print("\n【5b】深审修复:重试改善后早退(用 last_delta,非累积 max_delta)")
    a = FakeAgent()
    a.thought_chain = _DynChain(a)
    ctx = PipelineContext(agent=a, task="D",
                          context={"_reflect": {"max_retries": 3, "drift_threshold": 0.5}})
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(ctx))
    assert a.exec_count == 2, f"改善后应只重试 1 次(而非到上限 3),实得 {a.exec_count}"
    assert ctx.reflect["decision"] == "retry_accepted" and ctx.reflect["retries"] == 1, ctx.reflect
    assert ctx.verified["max_delta"] == 0.8 and ctx.verified["last_delta"] == 0.2
    print(f"  ✓ 早退:exec={a.exec_count} reflect={ctx.reflect['decision']} "
          f"(max_delta={ctx.verified['max_delta']} 仍高,但 last_delta={ctx.verified['last_delta']} 达标)")


def test_reflect_failsafe_rollback():
    print("\n【5】reflect fail-safe:重试自身出错 → 回滚到重试前结果")
    a = FakeAgent(nodes=[_Node("WARN", 0.8)], fail_on=2)
    ctx = PipelineContext(agent=a, task="X",
                          context={"_reflect": {"max_retries": 2, "drift_threshold": 0.5}})
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(ctx))
    assert a.exec_count == 2, "初次 + 1 次失败重试"
    assert ctx.result == "done:X#1", "回滚到重试前的好结果(重试不让结果变差)"
    assert ctx.reflect["decision"] == "retry_reverted" and ctx.reflect["retries"] == 1, ctx.reflect
    print(f"  ✓ 重试出错回滚:result={ctx.result!r} reflect={ctx.reflect}(fail-safe)")


def test_concurrent_ctx_isolation():
    """【P4 证伪】并发多任务打同一 agent:ctx 是 run_task 内**每任务新建的局部对象**、
    stage **顺序 await**(无 stage 并行),故可变 ctx **并发安全、不串**。

    范式原则五"不可变状态流转"反对的是"**跨任务共享**可变状态"(工具 A 改一半、工具 B
    读到不一致);PipelineContext 不属于该反模式——它不跨任务共享。本测试用 24 个并发任务
    + 每个 _execute await 让出控制权(最大化交错、逼出潜在串状态)来证伪。"""
    print("\n【6】P4 证伪:并发多任务 ctx 隔离(可变但不串)")

    class SlowAgent(FakeAgent):
        async def _execute(self, task, context, session_id, memories):
            await asyncio.sleep(0.005)
            return f"done:{task}", {"task_echo": task}

    a = SlowAgent()

    async def run_one(task):
        ctx = PipelineContext(agent=a, task=task, session_id=f"s_{task}")
        await CognitivePipeline(DEFAULT_STAGES).run(ctx)
        return task, ctx

    async def _main():
        tasks = [f"T{i}" for i in range(24)]
        results = await asyncio.gather(*[run_one(t) for t in tasks])
        for t, ctx in results:
            assert ctx.task == t, f"ctx.task 串了: {ctx.task} != {t}"
            assert ctx.result == f"done:{t}", f"ctx.result 串了: {ctx.result} != done:{t}"
            assert ctx.extra["task_echo"] == t, f"ctx.extra 串了: {ctx.extra}"
            assert ctx.route.get("target") == "self"
            assert ctx.trace == ["perceive", "recall", "plan", "route", "act", "verify", "reflect"]
        return len(results)

    n = asyncio.run(_main())
    print(f"  ✓ {n} 并发任务 ctx 零串——每个 ctx.result/task/extra/trace 都属自己的 task")
    print("    结论:ctx 每任务新建 + stage 顺序执行 → 可变但并发安全(P4 证实为伪风险)")


def test_cooperative_interrupt():
    """【P3】协作式中断:stop_event 置位 → 管线在下一 stage 边界 / reflect 重试前优雅停止。
    opt-in——默认 stop_event=None 时行为逐字不变。覆盖:(a)不置位跑满 (b)起步即停
    (c)中途 stage 边界停 (d)reflect 重试循环停(范式"每轮迭代检查中断"的本地对应)。"""
    import threading
    print("\n【7】P3 协作式中断:stop_event 在 stage 边界 / 重试迭代前优雅停")

    a = FakeAgent()
    ctx = PipelineContext(agent=a, task="T")
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(ctx))
    assert ctx.interrupted is False and len(ctx.trace) == 7, (ctx.interrupted, ctx.trace)

    ev = threading.Event(); ev.set()
    ctx2 = PipelineContext(agent=FakeAgent(), task="T", stop_event=ev)
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(ctx2))
    assert ctx2.interrupted is True and ctx2.trace == [] and ctx2.result is None, \
        (ctx2.interrupted, ctx2.trace, ctx2.result)

    ev2 = threading.Event()
    def _tripwire(c): ev2.set()
    _tripwire.stage_name = "tripwire"
    stages = DEFAULT_STAGES[:2] + [_tripwire] + DEFAULT_STAGES[2:]
    ctx3 = PipelineContext(agent=FakeAgent(), task="T", stop_event=ev2)
    asyncio.run(CognitivePipeline(stages).run(ctx3))
    assert ctx3.trace == ["perceive", "recall", "tripwire"], ctx3.trace
    assert ctx3.interrupted is True and ctx3.result is None, (ctx3.interrupted, ctx3.result)
    print(f"  ✓ 边界中断:无 stop 跑满 7;起步停 0 stage;中途停 trace={ctx3.trace}(后续未跑)")

    ev3 = threading.Event()
    class _StopOnRetryAgent(FakeAgent):
        def __init__(self, e):
            super().__init__(nodes=[_Node("WARN", 0.8)])
            self._e = e
        async def _execute(self, task, context, session_id, memories):
            self.exec_count += 1
            if self.exec_count == 2:
                self._e.set()
            return f"done:{task}#{self.exec_count}", {"call": self.exec_count}
    a4 = _StopOnRetryAgent(ev3)
    ctx4 = PipelineContext(agent=a4, task="R", stop_event=ev3,
                           context={"_reflect": {"max_retries": 5, "drift_threshold": 0.5}})
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(ctx4))
    assert a4.exec_count == 2, f"初次 act + 1 次重试后即中断(上限 5 未跑满),实得 {a4.exec_count}"
    assert ctx4.reflect["decision"] == "interrupted" and ctx4.reflect["retries"] == 1, ctx4.reflect
    assert ctx4.interrupted is True
    print(f"  ✓ 重试中断:exec={a4.exec_count}(上限5未满) reflect={ctx4.reflect['decision']} "
          f"→ 省下 4 次 LLM 调用")
    print("    结论:stop_event 在 stage 边界与重试迭代前生效;默认 None 时全部行为逐字不变")


def test_on_stage_callback():
    """【8】D1:`pipeline.run(on_stage)` 逐 stage 回调(opt-in);无回调=行为不变;回调异常 fail-safe。"""
    print("\n【8】D1 on_stage 回调:逐 stage 通知 + opt-in + fail-safe")
    seen = []
    ctx = PipelineContext(agent=FakeAgent(), task="T")
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(
        ctx, on_stage=lambda name, c: seen.append(name)))
    assert seen == ctx.trace == ["perceive", "recall", "plan", "route", "act", "verify", "reflect"], \
        (seen, ctx.trace)
    ctx2 = PipelineContext(agent=FakeAgent(), task="T")
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(ctx2))
    assert len(ctx2.trace) == 7 and ctx2.result == "done:T#1"
    def _boom(name, c):
        raise RuntimeError("回调故障")
    ctx3 = PipelineContext(agent=FakeAgent(), task="T")
    asyncio.run(CognitivePipeline(DEFAULT_STAGES).run(ctx3, on_stage=_boom))
    assert len(ctx3.trace) == 7 and ctx3.result == "done:T#1", "回调故障不拖垮管线"
    print(f"  ✓ on_stage 逐 stage 回调({len(seen)} 次)、无回调行为不变、回调异常 fail-safe")


def main():
    print("=" * 60)
    print("认知管线 · Cognitive Pipeline(v2 ② recall/act/verify/reflect)")
    print("=" * 60)
    test_default_pipeline()
    test_plan_route_stage()
    test_verify_stage()
    test_insertable_stage()
    test_exception_propagates()
    test_stage_events()
    test_route_skill_optin_inert()
    test_act_skill_dispatch_step2()
    test_run_task_confirm_suspend_step3()
    test_reflect_retry_optin()
    test_reflect_early_exit_on_improve()
    test_reflect_failsafe_rollback()
    test_concurrent_ctx_isolation()
    test_cooperative_interrupt()
    test_on_stage_callback()
    print("\n" + "=" * 60)
    print("✓ Step1 验证完成:PipelineContext + 顺序 + 可插桩 + 异常透传 + 并发隔离 + 中断 + on_stage")
    print("=" * 60)


if __name__ == "__main__":
    main()
