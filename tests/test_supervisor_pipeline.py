"""P1-1 · 主管 pipeline 化回归。

主管不再重写 run_task,路由分发拆成 8 个 pipeline stage
(recall→meta_check→schedule→route→plan→dispatch→replan→aggregate),走 base 统一模板
`_run_task_core`。本文件锁定:

  [1] 结构:SupervisorAgent.run_task 即基类模板;主管 pipeline 是 SUPERVISOR_STAGES;
  [2] 元问题短路:meta_check 自答,后续 stage 跳过,内部标记不外泄;
  [3] 单 Agent 分发:返回体顶层键与旧实现逐字一致(result/schedule/plan/process_id);
  [4] stub 兜底:无关键词命中 + stub → 兜底派发 assistant(不经自答);
  [5] 统一 execution.completed:多步并行任务也只发**一条**任务级事件、
      agent_id=supervisor(文稿 Step 4:删手动 bus.publish);blocked 子任务 →
      顶层 status=blocked 且执行事件 blocked=True(未完成不沉淀真值);
  [6] 流式:run_task_stream 逐 stage 出 8 个进度事件;子 Agent 的 act token 随
      contextvar 传播流出(delta 非空);stop_event 预置 → interrupted;
  [7] 恢复记账:经 base 生命周期钩子,started/finished 配对、异常路径记 failed。

全离线:ws.needs_search 关掉联网;router 强制 stub(共享实例,五个 Agent 同源)。
"""
import asyncio
import sys
import tempfile
import threading
import types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import foxclaw.core.web_search as ws
ws.needs_search = lambda q: False

from foxclaw import Foxclaw
from foxclaw.agents.base import CognitiveAgent, HarnessBlockedError
from foxclaw.agents.agents import SupervisorAgent
from foxclaw.pipeline import SUPERVISOR_STAGES

EXPECTED_STAGES = ["recall", "meta_check", "schedule", "route", "plan", "dispatch", "replan", "aggregate"]


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


def _names():
    return [getattr(s, "stage_name", getattr(s, "__name__", "?")) for s in SUPERVISOR_STAGES]


def main():
    print("=" * 60)
    print("P1-1 · 主管 pipeline 化回归")
    print("=" * 60)

    print("\n【1】结构:主管走统一模板,不再重写 run_task")
    check(SupervisorAgent.run_task is CognitiveAgent.run_task,
          "SupervisorAgent.run_task 即基类模板(P1-1 达成)")
    check(SupervisorAgent._cognitive_pipeline is not CognitiveAgent._cognitive_pipeline,
          "主管覆盖 _cognitive_pipeline(路由分发走 SUPERVISOR_STAGES)")
    check(_names() == EXPECTED_STAGES, f"主管 stage 序列={EXPECTED_STAGES}(实为 {_names()})")

    os_ = Foxclaw(data_dir=tempfile.mkdtemp(prefix="foxclaw_svpipe_"))
    os_.router.provider = "stub"
    os_.router._client = None
    sup = os_.supervisor

    print("\n【2】元问题短路:meta_check 自答,内部标记不外泄")
    r = asyncio.run(sup.run_task("你是谁,能做什么"))
    res = r["result"]
    check(res.get("dispatched_to") == "supervisor" and res.get("self_answered") is True,
          "元问题 → 主管自答(dispatched_to=supervisor, self_answered=True)")
    check("_meta_answered" not in r and "_self_answer" not in r,
          "内部短路标记不外泄到返回体")
    check(r.get("agent") == "supervisor" and "session_id" in r and "thought_summary" in r,
          "返回体由 base 模板统一组装(agent/session_id/thought_summary)")

    print("\n【3】单 Agent 分发:返回体顶层键与旧实现逐字一致")
    r = asyncio.run(sup.run_task("帮我做个预算分析"))
    res = r["result"]
    check(res.get("dispatched_to") == "finance", f"关键词命中 → finance(实为 {res.get('dispatched_to')})")
    for k in ("schedule", "plan", "process_id"):
        check(k in r, f"顶层键 {k} 保留(与旧主管返回体一致)")
    check(r["plan"].get("multi_step") is False, "单步任务 plan.multi_step=False")
    check(res.get("blocked") is False, "正常分发 blocked=False")

    print("\n【4】stub 兜底:无关键词命中 + stub → 主管自处理(助手已下放为技能)")
    r = asyncio.run(sup.run_task("解释一下什么是航天器"))
    res = r["result"]
    check(res.get("dispatched_to") == "supervisor" and not res.get("self_answered"),
          f"stub 下未命中 → 主管自身处理(实为 {res.get('dispatched_to')})")

    print("\n【5】统一 execution.completed(文稿 Step 4)")
    spine = os_.event_spine
    before = len(spine.history("execution.completed"))
    asyncio.run(sup.run_task("同时查今天的日程、分析本月理财、总结这篇论文"))
    evts = spine.history("execution.completed")
    new_evts = evts[before:]
    check(len(new_evts) == 1,
          f"并行 3 子任务只发 1 条任务级 execution.completed(实为 {len(new_evts)} 条)"
          "——逐子任务手动 publish 已删")
    check(new_evts and new_evts[0]["payload"].get("agent_id") == "supervisor",
          "任务级事件归因 supervisor(子 Agent 带 _no_emit 不双发)")

    print("\n【5b】blocked 子任务 → 顶层 status=blocked + 事件 blocked=True(不沉淀真值)")
    async def _boom(self, task, context, session_id, memories):
        raise HarnessBlockedError(types.SimpleNamespace(reason="模拟越界"), None)
    os_.finance._execute = types.MethodType(_boom, os_.finance)
    before = len(spine.history("execution.completed"))
    r = asyncio.run(sup.run_task("帮我做个预算分析"))
    check(r["result"].get("blocked") is True, "子 Agent 被拦 → result.blocked=True")
    check(r.get("status") == "blocked",
          "被拦提升为顶层 status=blocked(与 base 模板 skill_status 机制一致)")
    new_evts = spine.history("execution.completed")[before:]
    check(len(new_evts) == 1 and new_evts[0]["payload"].get("blocked") is True,
          "被拦任务的执行事件 blocked=True → 真值链不沉淀")
    del os_.finance._execute

    print("\n【6】流式:run_task_stream 八 stage + 子 Agent token 传播 + 中断")
    events = list(sup.run_task_stream("帮我做个预算分析"))
    stages = [e["stage"] for e in events if "stage" in e]
    deltas = [e["delta"] for e in events if "delta" in e]
    results = [e["result"] for e in events if "result" in e]
    check(stages == EXPECTED_STAGES, f"流式逐 stage 进度=主管八 stage(实为 {stages})")
    check(len(results) == 1 and results[0].get("agent") == "supervisor",
          "恰一个最终 result,agent=supervisor")
    check(results[0]["result"].get("dispatched_to") == "finance", "流式与非流式路由结论一致")
    check(len(deltas) > 0,
          "delta 非空:子 Agent 的 act token 随 _act_stream contextvar 传播流出(文稿坑5实测反转)")
    check("".join(deltas) == str(results[0]["result"].get("sub_result")),
          "流式 token 拼接 == 子 Agent 最终结果(透传忠实)")

    ev = threading.Event(); ev.set()
    ev_events = list(sup.run_task_stream("这个任务会被中断", stop_event=ev))
    ev_results = [e["result"] for e in ev_events if "result" in e]
    check(ev_results and ev_results[0].get("interrupted") is True,
          "stop_event 预置 → result.interrupted=True(stage 边界优雅停)")
    check(not [e for e in ev_events if "stage" in e], "起步即停:无 stage 进度事件")

    print("\n【7】恢复记账:base 生命周期钩子,started/finished 配对")
    calls = []

    class _Spy:
        def task_started(self, task_id, description, session_id, channel=""):
            calls.append(("started", task_id))
        def task_finished(self, sid, status="done"):
            calls.append(("finished", sid, status))

    sup._recovery = _Spy()
    asyncio.run(sup.run_task("帮我做个预算分析", context={"channel": "test"}))
    check(calls[0][0] == "started" and calls[1][0] == "finished" and calls[1][2] == "done",
          f"正常任务:started→finished(done) 配对(实为 {calls})")
    check(calls[0][1] == calls[1][1], "started/finished 同一 task_id(可配对)")

    calls.clear()
    async def _raise(self, task, context=None, **kw):
        raise RuntimeError("子 Agent 崩溃")
    sup._subagents["finance"].run_task = _raise
    try:
        asyncio.run(sup.run_task("帮我做个预算分析"))
        raised = False
    except RuntimeError:
        raised = True
    check(raised, "子 Agent 未兜底异常向上传播(不吞)")
    check(calls[0][0] == "started" and calls[1] == ("finished", calls[0][1], "failed"),
          f"异常任务记 failed(区别于正常完成)(实为 {calls})")

    print("\n" + "=" * 60)
    print("P1-1 主管 pipeline 化回归通过")
    print("=" * 60)


def test_supervisor_pipeline():
    main()


if __name__ == "__main__":
    main()
