"""
统一任务生命周期(P1:5 个 run_task 收敛为 1)守卫测试 —— 全离线。

覆盖:
  1. 结构收敛:4 个领域 Agent 复用 base 的模板 run_task(不各自重写),各自实现 _execute;
     SupervisorAgent 作为路由器合法地保留自己的 run_task。
  2. 统一异常兜底:_execute 抛 HarnessBlockedError → 模板统一返回结构化错误(一处定义,全 Agent 生效)。
  3. 领域钩子生效:各 Agent 的 entities/recall 前缀/召回条数按覆盖生效(行为逐字保留)。
"""
import os, sys, asyncio, tempfile, shutil, types
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_EMBEDDING_MODEL"] = "foxclaw-nonexistent-fast"
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.agents.base import CognitiveAgent, HarnessBlockedError
from foxclaw.agents.agents import (
    FinanceAgent, OfficeAgent, SupervisorAgent)


def test_structural_convergence():
    print("\n【1】结构收敛:全部 Agent 复用模板 run_task(主管经 P1-1 pipeline 化收敛)")
    for A in (FinanceAgent, OfficeAgent):
        assert A.run_task is CognitiveAgent.run_task, f"{A.__name__} 不应重写 run_task"
        assert A._execute is not CognitiveAgent._execute, f"{A.__name__} 应实现 _execute"
    # 【P1-1】主管也不再重写 run_task:路由分发拆成 SUPERVISOR_STAGES 七个 pipeline
    # stage,走 base 统一模板(获得 hooks/流式/中断/统一 emit)。主管的差异收敛为
    # _cognitive_pipeline 覆盖 + 领域钩子,而非另起生命周期。(本断言此前锁的是
    # "主管保留自身 run_task" 的旧结构,P1-1 落地时同步翻转——又一处"测试锁旧行为"。)
    assert SupervisorAgent.run_task is CognitiveAgent.run_task, \
        "P1-1:主管不再重写 run_task(路由分发在 pipeline stage 里)"
    assert SupervisorAgent._cognitive_pipeline is not CognitiveAgent._cognitive_pipeline, \
        "主管应覆盖 _cognitive_pipeline 使用 SUPERVISOR_STAGES"
    print("  ✓ 5 个 Agent 共用唯一 run_task 模板;主管差异收敛为 pipeline 覆盖(P1-1)")


def test_domain_hooks():
    print("\n【2】领域钩子:entities / recall 前缀 / 召回条数 逐字保留")
    # 钩子是纯函数,可脱离实例校验(lambda(self,task))
    assert FinanceAgent._task_entities(None, "x") == ["财务", "收支", "储蓄"]
    assert FinanceAgent._recall_query(None, "买车") == "财务 买车"
    # 行政并入办公:同一 Agent 同时覆盖学习 + 日程两个域
    assert OfficeAgent._recall_query(None, "开会") == "学习 知识 日程 行程 开会"
    assert OfficeAgent._task_entities(None, "x") == ["学习", "知识", "文档", "日程", "行程", "会议"]
    assert OfficeAgent._recall_topk == 8, "办公学习 Agent 覆盖为广泛召回 8"
    assert CognitiveAgent._recall_topk == 5, "基类默认仍是 5"
    print("  ✓ finance/admin/office 的 entities、recall 前缀、top_k 均按覆盖生效")


def test_unified_block_handling():
    print("\n【3】统一异常兜底:_execute 抛 HarnessBlockedError → 结构化错误")
    d = tempfile.mkdtemp(prefix="life_")
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)

        async def boom(self, task, context, session_id, memories):
            raise HarnessBlockedError(types.SimpleNamespace(reason="模拟越界"), None)

        # 只替换 finance 的领域执行,模板的兜底应把它转成结构化错误
        os_.finance._execute = types.MethodType(boom, os_.finance)
        r = asyncio.run(os_.finance.run_task("危险任务"))
        assert r.get("error") == "harness_blocked", f"应兜底为 harness_blocked,实得 {r}"
        assert r.get("reason") == "模拟越界" and "session_id" in r
        print(f"  ✓ 模板统一兜底: {r}")
        asyncio.run(os_.aclose())
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_finance_verify_before_recommend():
    """【4·P5】记忆验证优先:财务事实以**当前账本(live analyze)**为准,召回记忆仅作历史上下文。
    锁定 finance._execute 的 verify-before-recommend 设计——即便召回了**过时**记忆,权威事实源
    仍是当前交易(`fin.analyze()`),记忆被显式降级为"历史财务记录"。防未来重构把过时记忆当事实源
    (呼应范式"记忆是快照,非事实")。评估结论见方案 P5:该设计已存在,本测试是把它锁死的回归护栏。"""
    print("\n【4】P5 记忆验证优先:财务事实以当前账本为准、记忆仅历史上下文")
    d = tempfile.mkdtemp(prefix="p5_")
    os_ = None
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        fin = os_.finance
        # 给账本内核灌入真实交易 → analyze() 有据可依(当前数据源存在)
        os_.finance_runtime.import_csv(
            str(Path(__file__).parent.parent / "examples" / "sample_transactions.csv"))
        # 捕获传给 LLM 的 prompt(不真调模型)
        captured = {}
        async def _capture(self, task, messages):
            captured["prompt"] = messages[-1]["content"]
            return "（模拟回答）"
        fin._call_llm_web = types.MethodType(_capture, fin)
        # 一条**过时**召回记忆:若被当作事实会误导用户
        stale = [{"content": "历史:上月预算结余 8000 元", "id": "m1"}]
        asyncio.run(fin._execute("这个月还能花多少?", {}, "s1", stale))
        p = captured["prompt"]
        assert "基于真实交易,请据此回答" in p, "缺 live analyze() 权威事实源(verify-before-recommend 退化)"
        assert "历史财务记录" in p and "8000" in p, "召回记忆应作历史上下文出现,而非当前事实"
        assert p.index("历史财务记录") < p.index("基于真实交易"), \
            "结构应为:历史记忆在前、当前账本权威在后(措辞上 live『请据此回答』压过历史记忆)"
        print("  ✓ live analyze() 作权威源『请据此回答』;过时记忆降级为『历史财务记录』(P5 已锁定)")
    finally:
        if os_ is not None:
            asyncio.run(os_.aclose())
        shutil.rmtree(d, ignore_errors=True)


def test_run_task_stream():
    """【5·D1】run_task 流式化:同步 generator 逐 stage 进度 + act 逐 token + 最终结果,且与非流式
    run_task **行为一致**(共享 `_run_task_core`)。关键验证:`_act_stream` contextvar 真的跨
    worker 线程 / asyncio 边界传到 `_call_llm`(否则一个 delta token 都不会有)。"""
    print("\n【5】D1 run_task 流式化:stage 进度 + act token + 结果")
    d = tempfile.mkdtemp(prefix="d1_")
    os_ = None
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        # 单 Agent 认知管线(7 stage)用领域 Agent 验证:主管走的是 meta/schedule/dispatch
        # 另一条管线,不能拿它当"标准 stage 序列"的样本。
        ag = os_.office
        ag.router.provider = "stub"  # 强制 stub(is_stub = provider=='stub'):离线确定性(不受本机
                                     # 是否配了 LLM key 影响),且 stub 的 router.stream 走 _chunk_text
                                     # 无损分块 → 可断言"流式 token 拼接 == 最终答案"

        # (a) 正常流式:collect 所有事件
        events = list(ag.run_task_stream("帮我总结一下今天"))
        stages = [e["stage"] for e in events if "stage" in e]
        deltas = [e["delta"] for e in events if "delta" in e]
        results = [e["result"] for e in events if "result" in e]
        assert stages == ["perceive", "recall", "plan", "route", "act", "verify", "reflect"], stages
        # delta 非空 = _act_stream contextvar 成功跨线程/asyncio 传到了 _call_llm(核心正确性证明)
        assert len(deltas) > 0, "act 应逐 token 出字;为空说明 contextvar 没传到 _call_llm"
        assert len(results) == 1, f"应恰有一个最终 result,实得 {len(results)}"
        res = results[0]
        assert res.get("agent") == ag.AGENT_ID and "session_id" in res and "result" in res
        # 拼接的流式 token == 最终答案(token 透传忠实;_chunk_text 无损)
        assert "".join(deltas) == res["result"], "流式 token 拼接应等于最终答案"

        # (b) 行为保持:非流式 run_task 仍正常、返回体无 delta 泄漏
        plain = asyncio.run(ag.run_task("帮我总结一下今天"))
        assert "result" in plain and "delta" not in plain and plain.get("agent") == ag.AGENT_ID

        # (c) stop_event 预置 → 流式在起步即停:无 stage、无 delta、result.interrupted
        import threading as _th
        ev = _th.Event(); ev.set()
        ev_events = list(ag.run_task_stream("这个任务会被中断", stop_event=ev))
        ev_stages = [e for e in ev_events if "stage" in e]
        ev_results = [e["result"] for e in ev_events if "result" in e]
        assert ev_stages == [], f"预置 stop → 0 个 stage 事件,实得 {ev_stages}"
        assert len(ev_results) == 1 and ev_results[0].get("interrupted") is True, ev_results

        # (d) fail-safe:act 流式若抛异常 → 降级非流式 complete、返回正常(不崩、不中断任务)。
        # 锁住 _call_llm docstring 承诺的"流式任何异常 → fail-safe 降级 complete"。
        def _boom(*a, **k):
            raise RuntimeError("流式炸了")
        ag.router.stream = _boom
        from foxclaw.agents.base import _act_stream as _AS
        async def _one_call():
            tok = _AS.set({"on_token": lambda c: None, "stop_event": None})
            try:
                return await ag._call_llm([{"role": "user", "content": "x"}])
            finally:
                _AS.reset(tok)
        out = asyncio.run(_one_call())
        assert isinstance(out, str) and out, "流式失败应 fail-safe 降级 complete(返回非空串),而非崩"

        print(f"  ✓ stage={len(stages)} delta={len(deltas)} result=1;token 拼接==答案;"
              f"非流式不受影响;预置 stop 起步即停;流式异常 fail-safe 降级 complete")
    finally:
        if os_ is not None:
            asyncio.run(os_.aclose())
        shutil.rmtree(d, ignore_errors=True)


def test_interrupted_task_not_sunk():
    """【6·B3 深审 G2】被 stop_event 中断的任务 = 未完成,绝不沉淀为已完成真值(blocked=True)。
    深审发现:_emit_execution 的 blocked 原只看 skill_status,中断任务(skill_status=None)会
    blocked=False → 部分/空结果被 TruthPipeline 当完成写进 HMR 共享记忆(违反 P5/Principle07)。
    修复后 blocked 联动 ctx.interrupted。这条路径由 B3 的 /api/task/stream(断连 set stop_event)
    首次在生产可达,故 happy-path 测不到、深审才暴露。"""
    print("\n【6】B3 深审 G2:中断任务不沉淀真值(blocked=True)")
    d = tempfile.mkdtemp(prefix="g2_")
    os_ = None
    try:
        import threading as _th
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        ag = os_.supervisor
        ag.router.provider = "stub"
        captured = {}
        orig = ag._emit_execution
        def _spy(task, result, blocked, context):
            captured["blocked"] = blocked
            return orig(task, result, blocked=blocked, context=context)
        ag._emit_execution = _spy
        # (a) 预置 stop → 中断 → blocked=True(不沉淀部分/空结果为真值)
        ev = _th.Event(); ev.set()
        r = asyncio.run(ag.run_task("会被中断的任务", stop_event=ev))
        assert r.get("interrupted") is True, r
        assert captured.get("blocked") is True, "中断任务必须 blocked=True(不把部分/空结果当完成真值沉淀)"
        # (b) 正常任务 → blocked=False(behavior-preserving:照常沉淀)
        captured.clear()
        r2 = asyncio.run(ag.run_task("正常任务"))
        assert "interrupted" not in r2 and captured.get("blocked") is False, \
            "正常完成任务 blocked=False(行为逐字不变,照常沉淀真值)"
        print("  ✓ 中断→blocked=True(不沉淀);正常→blocked=False(照常沉淀,行为不变)")
    finally:
        if os_ is not None:
            asyncio.run(os_.aclose())
        shutil.rmtree(d, ignore_errors=True)


def test_reasoning_lifecycle_hooks():
    """【7·E2】认知管线生命周期钩子:pre_reasoning(agent_start)可注入推理上下文(前插进 LLM);
    task_complete(agent_end)只读观测;默认无钩子=行为逐字不变。这两个语义名是此前从未 fire 的
    孤儿 agent_start/agent_end 的别名——E2 顺带复活了它们并给认知管线 run_task 装上钩子。"""
    print("\n【7】E2 生命周期钩子:pre_reasoning 注入 + task_complete 观测")
    d = tempfile.mkdtemp(prefix="e2_")
    os_ = None
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        ag = os_.office          # 单 Agent 认知管线(见上一条注释)
        ag.router.provider = "stub"
        seen = {}
        def _cap_complete(system, messages, max_tokens, agent_id):
            seen["messages"] = messages
            return "（stub 回答）"
        ag.router.complete = _cap_complete

        # (a) 无钩子基线:不注入(behavior-preserving)
        asyncio.run(ag.run_task("基线任务"))
        base_msgs = seen.get("messages", [])
        assert base_msgs and "【E2注入】" not in base_msgs[0].get("content", ""), \
            f"无 pre_reasoning 钩子时不应注入,实得 {base_msgs[:1]}"

        # (b) 注册 pre_reasoning(注入)+ task_complete(观测)
        completed = []
        ag.hooks.register("pre_reasoning", lambda c: {"additional_context": "【E2注入】优先考虑省钱"})
        ag.hooks.register("task_complete", lambda c: completed.append(c.get("result")))
        asyncio.run(ag.run_task("帮我看看要不要买"))
        msgs = seen.get("messages", [])
        joined = " ".join(m.get("content", "") for m in msgs)
        assert "【E2注入】优先考虑省钱" in joined, f"pre_reasoning 注入应进入 LLM messages,实得 {msgs}"
        # A3:注入须合并进 user 消息,**不得产生连续两条 user**(模型无关铁律,某些端点会拒连续 user)
        roles = [m.get("role") for m in msgs]
        assert not any(roles[i] == roles[i + 1] == "user" for i in range(len(roles) - 1)), \
            f"注入不应产生连续两条 user 消息(违反模型无关铁律),roles={roles}"
        assert len(completed) == 1, f"task_complete 应触发一次并收到 result,实得 {len(completed)}"

        # (c) HarnessBlock 结束路径:task_complete 也触发(观测被拦截任务,带 error)
        import types
        async def _boom(self, task, context, session_id, memories):
            raise HarnessBlockedError(types.SimpleNamespace(reason="E2越界"), None)
        ag._execute = types.MethodType(_boom, ag)
        completed.clear()
        rb = asyncio.run(ag.run_task("越界任务"))
        assert rb.get("error") == "harness_blocked", rb
        assert len(completed) == 1, "task_complete 应在 HarnessBlock 结束路径也触发(观测被拦截任务)"
        print("  ✓ 无钩子不注入(behavior-preserving);pre_reasoning 注入前插;task_complete 正常+拦截路径都观测")
    finally:
        if os_ is not None:
            asyncio.run(os_.aclose())
        shutil.rmtree(d, ignore_errors=True)


def test_preference_priority_recall():
    """【8·偏好优先召回】明确偏好标 tag=preference(shared 持久)+ **独立于语义 query** 优先注入。
    核心对照:偏好与当前任务**语义完全不相关**时,纯语义 recall 召不到,但偏好召回仍注入——
    修复"越用越懂你"断在召回链的洞。默认开;FOXCLAW_PREF_CAPTURE=0 关采集。"""
    print("\n【8】偏好优先召回:语义不相关也注入")
    d = tempfile.mkdtemp(prefix="pref_")
    os_ = None
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        ag = os_.office          # 单 Agent 认知管线(见上一条注释)
        ag.router.provider = "stub"
        seen = {}
        def _cap(system, messages, max_tokens, agent_id):
            seen["messages"] = messages; return "（stub）"
        ag.router.complete = _cap

        # (a) 明确偏好表达 → 存成带 preference tag(shared)
        asyncio.run(ag.run_task("我喜欢简洁直接的回答，不要长篇大论"))
        prefs = ag.hmr.recall_by_tag(agent_id="supervisor", tag="preference", limit=8)
        assert any("简洁" in p.get("content", "") for p in prefs), f"偏好应带 preference tag 存下,实得 {prefs}"

        # (b) 发**语义完全不相关**的任务 → 偏好仍注入 prompt(纯语义召回此时召不到)
        seen.clear()
        asyncio.run(ag.run_task("解释一下光合作用的原理"))
        joined = " ".join(m.get("content", "") for m in seen.get("messages", []))
        assert "用户偏好" in joined and "简洁" in joined, \
            f"语义不相关任务也应注入偏好(越用越懂你),实得 {seen.get('messages')}"

        # (c) 无偏好信号的普通任务不误存(保守采集)
        before = len(ag.hmr.recall_by_tag(agent_id="assistant", tag="preference"))
        asyncio.run(ag.run_task("光合作用发生在植物的哪个部位"))
        after = len(ag.hmr.recall_by_tag(agent_id="assistant", tag="preference"))
        assert after == before, "无偏好信号的任务不应新增偏好"

        # (d) 关采集开关 → 偏好句也不存(可关)
        os.environ["FOXCLAW_PREF_CAPTURE"] = "0"
        try:
            asyncio.run(ag.run_task("我喜欢用表格展示数据"))
            got = ag.hmr.recall_by_tag(agent_id="assistant", tag="preference", limit=20)
            assert not any("表格" in p.get("content", "") for p in got), "关采集后不应存偏好"
        finally:
            os.environ.pop("FOXCLAW_PREF_CAPTURE", None)

        # (e) 对话路径(stream_reply)也注入偏好——之前只测了任务路径(深审 F2:claim 必测)
        seen_stream = []
        def _cap_stream(system, messages, *a, **k):
            seen_stream.append(messages); yield "（stub）"
        ag.router.stream = _cap_stream
        list(ag.stream_reply("随便讲点什么"))   # 语义与"简洁"偏好不相关
        js = " ".join(m.get("content", "") for m in (seen_stream[0] if seen_stream else []))
        assert "用户偏好" in js and "简洁" in js, f"对话路径(stream_reply)也应注入偏好,实得 {seen_stream}"

        # (f) 去重:同一偏好存多条,召回只返一条(深审 F1)
        for _ in range(3):
            ag._store_memory("用户偏好:我喜欢简洁直接的回答，不要长篇大论",
                             mem_type="preference", tags=["preference"], shared=True)
        got = ag.hmr.recall_by_tag(agent_id="assistant", tag="preference", limit=8)
        same = [p for p in got if "简洁直接的回答" in p.get("content", "")]
        assert len(same) == 1, f"重复偏好召回应去重为 1 条,实得 {len(same)}"

        # (g) 删偏好途径(F3):存唯一偏好 → list 找到(带 id)→ delete → 确认删除
        ag._store_memory("用户偏好:请用英文回复", mem_type="preference", tags=["preference"], shared=True)
        target = [p for p in ag.list_preferences() if "英文" in p.get("content", "")]
        assert target and target[0].get("id"), f"唯一偏好应可列出带 id,实得 {target}"
        assert ag.delete_preference(target[0]["id"]), "删偏好应成功"
        gone = [p for p in ag.list_preferences() if "英文" in p.get("content", "")]
        assert not gone, "删除后该偏好不应再出现(F3:误存可清理)"
        print("  ✓ 偏好存 tag;两路径注入;去重;不误存;可关;可 list/删除(F3)")
    finally:
        if os_ is not None:
            asyncio.run(os_.aclose())
        shutil.rmtree(d, ignore_errors=True)


def main():
    print("=" * 60)
    print("统一任务生命周期 · run_task 收敛(P1)")
    print("=" * 60)
    test_structural_convergence()
    test_domain_hooks()
    test_unified_block_handling()
    test_finance_verify_before_recommend()
    test_run_task_stream()
    test_interrupted_task_not_sunk()
    test_reasoning_lifecycle_hooks()
    test_preference_priority_recall()
    print("\n" + "=" * 60)
    print("✓ P1 验证完成:5 run_task→1 模板 + 领域 _execute 钩子 + 统一兜底 + P5 记忆验证优先")
    print("=" * 60)


if __name__ == "__main__":
    main()
