"""批次 6 · P2 层 + P3-2 测试

  [1] P2-2:compose 遇 needs_confirmation → 暂停不回滚(pending),blocked 仍回滚(对照)
  [2] P2-2:断点续跑 resume_state(第一步不重跑)
  [3] P2-2:AHSPipeline success 判定区分 pending + is_pending 属性
  [4] P2-1:run_async 返回结果 + 不阻塞事件循环(并发两个慢 handler < 2x 串行)+ 同步 run 兼容
  [5] P2-3:idempotency 追加日志(3 次 mark = 3 行)+ compact 合并快照 + 重启回放
  [6] P2-3:旧快照格式兼容 + 容量淘汰发 delete 日志(重启不复活)
  [7] P2-3:持久化失败发 idempotency.persist_failed 事件 + stats 计数
  [8] P2-4:plan_recovery 分类(retryable/notify_user/needs_manual)+ recovery.plan 事件
  [9] P2-4:start_count 递增 + compact_log 用 max(started,finished) 不丢刚完成的 + fsync=False 可用
  [10] P3-2:/api/ahs/run 走 run_async,暴露 is_pending/pending_confirmation
"""
import asyncio
import builtins
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

FAILS = []


def check(cond, label):
    if cond:
        print(f"  ✓ {label}")
    else:
        FAILS.append(label)
        print(f"  ✗ {label}")


from foxclaw.skills import SkillRegistry, SkillSpec, SkillAdapter
from foxclaw.skills.pipeline import compose, plan, AHSPipeline


def _make_pending_registry(counter: dict):
    """两个 skill:第一个必成功,第二个 destructive → 未确认时 needs_confirmation。

    注意本仓权限模型(permission.py Stage4):strict 模式下 side_effect ∈
    {write, send, pay} 一律 ask。所以第一步用 read(放行),destructive 的
    send_email 留给第二步制造 pending——文稿骨架假设 write 可直接成功,
    与本仓 Stage4 不符,按真实模型修正。"""
    reg = SkillRegistry()

    def _create(title=""):
        counter["create"] = counter.get("create", 0) + 1
        return {"created": title}

    reg.register(SkillSpec(
        id="create_event", name="创建日程", description="创建日程",
        kind="local", triggers=["创建", "日程"], side_effect="read",
        handler=_create, params={"title": "str"}))
    reg.register(SkillSpec(
        id="send_email", name="发邮件", description="发邮件",
        kind="local", triggers=["发送", "邮件"], side_effect="send",
        destructive=True,
        handler=lambda to="": {"sent": to}, params={"to": "str"}))
    return reg


def test_compose_pending():
    print("\n【1】P2-2:needs_confirmation → 暂停(pending),不回滚")
    reg = _make_pending_registry({})
    adp = SkillAdapter(reg)
    p = plan("创建日程 然后 发送邮件", reg)
    check(len(p.tasks) == 2 and p.tasks[0].suggested_skill == "create_event"
          and p.tasks[1].suggested_skill == "send_email",
          f"前置:计划两步且路由正确(实为 {[(t.id, t.suggested_skill) for t in p.tasks]})")
    res = compose(p, reg, adp, confirmed=False)
    check(res.is_pending, "待确认 → is_pending")
    check(res.pending_confirmation["skill_id"] == "send_email",
          "pending 定位到 send_email")
    check(not res.rolled_back and res.rollback_log == [],
          "pending 不触发回滚(rolled_back=False, rollback_log 空)")
    check(not res.is_failed and res.failed == [],
          "pending 不算失败(failed 空)")
    check(len(res.completed_steps) == 1
          and res.completed_steps[0][1] == "create_event",
          "第一步(create_event)已完成并存入 completed_steps")
    check(res.pending_confirmation["resume_at"] == "T2"
          and len(res.pending_confirmation["completed_steps"]) == 1,
          "pending_confirmation 带断点现场(resume_at=T2 + completed_steps)")
    check(res.final_output.get("pending") is True
          and res.final_output.get("completed") == ["T1"],
          "final_output 标记 pending + 已完成列表")


def test_compose_failed_still_rolls_back():
    print("\n【1b】对照:P2-2 后真失败(not_connected)仍触发回滚")
    reg = SkillRegistry()
    rolled = []
    reg.register(SkillSpec(
        id="create_event", name="创建日程", description="创建日程",
        kind="local", triggers=["创建", "日程"], side_effect="write",
        handler=lambda title="": {"created": title}, params={"title": "str"},
        compensate=lambda out: rolled.append(out)))
    reg.register(SkillSpec(
        id="send_email", name="发邮件", description="发邮件",
        kind="local", triggers=["发送", "邮件"], side_effect="send",
        handler=None,
        params={"to": "str"}))
    adp = SkillAdapter(reg)
    res = compose(plan("创建日程 然后 发送邮件", reg), reg, adp, confirmed=True)
    check(res.is_failed and not res.is_pending,
          "not_connected → failed 而非 pending")
    check(res.rolled_back and len(rolled) == 1,
          "关键路径失败 → 逆序回滚已执行的副作用步骤(补偿被调 1 次)")


def test_compose_resume():
    print("\n【2】P2-2:断点续跑 resume_state(第一步不重跑)")
    counter = {}
    reg = _make_pending_registry(counter)
    adp = SkillAdapter(reg)
    p = plan("创建日程 然后 发送邮件", reg)
    res1 = compose(p, reg, adp, confirmed=False)
    check(res1.is_pending, "前置:第一次 compose 停在 pending")
    res2 = compose(p, reg, adp, confirmed=True,
                   resume_state=res1.pending_confirmation)
    check(not res2.is_pending and not res2.is_failed,
          "确认后续跑:不再 pending、不失败")
    check(counter.get("create") == 1,
          f"第一步不重跑(create 调用数=1,实为 {counter.get('create')})")
    check(len(res2.completed_steps) == 2,
          "续跑后 completed_steps=2(恢复的 1 + 新执行的 1)")
    check(res2.final_output.get("sent") is not None
          or "send_email" in res2.final_output,
          f"续跑真正执行了 send_email(final_output={res2.final_output})")


def test_ahs_pipeline_pending():
    print("\n【3】P2-2:AHSPipeline success 判定区分 pending")
    reg = _make_pending_registry({})
    pipe = AHSPipeline(reg, SkillAdapter(reg))
    result = pipe.run("创建日程 然后 发送邮件", confirmed=False)
    check(result.success is False, "pending 时 success=False(外向结果与旧一致)")
    check(result.is_pending and result.pending_confirmation is not None,
          "PipelineResult.is_pending / pending_confirmation 可用")
    check("待确认" in result.summary, f"summary 指出待确认(实为 {result.summary})")
    comp = result.composition
    check(comp["pending_confirmation"]["skill_id"] == "send_email"
          and comp["completed_steps"] == ["T1"]
          and comp["rolled_back"] is False,
          "composition 字典带 pending_confirmation/completed_steps,未回滚")


def test_run_async():
    print("\n【4】P2-1:run_async 异步入口")
    reg = SkillRegistry()
    reg.register(SkillSpec(
        id="calc", name="计算器", description="计算",
        kind="local", triggers=["计算"], side_effect="read",
        handler=lambda expression="": {"result": eval(expression)},
        params={"expression": "str"}))

    def _slow(expression=""):
        time.sleep(0.5)
        return {"result": eval(expression)}
    reg.register(SkillSpec(
        id="slow", name="慢计算", description="慢计算",
        kind="local", triggers=["慢"], side_effect="read",
        handler=_slow, params={"expression": "str"}))
    pipe = AHSPipeline(reg, SkillAdapter(reg))

    async def _main():
        r = await pipe.run_async("计算 1+1")
        check(r.task == "计算 1+1" and (r.success or r.is_pending),
              "run_async 返回 PipelineResult")
        t0 = time.perf_counter()
        await asyncio.gather(pipe.run_async("慢 1+1"), pipe.run_async("慢 2+2"))
        elapsed = time.perf_counter() - t0
        check(elapsed < 0.9,
              f"并发两个慢 run_async 耗时 {elapsed:.2f}s < 0.9s(未阻塞事件循环)")
    asyncio.run(_main())
    r_sync = pipe.run("计算 1+1")
    check(r_sync.task == "计算 1+1", "同步 run 仍可用(向后兼容)")


from foxclaw.core.idempotency import IdempotencyGuard


class _Spine:
    def __init__(self):
        self.events = []

    def publish(self, event, payload, source=""):
        self.events.append((event, payload))


def test_idem_append_log():
    print("\n【5】P2-3:追加日志(O(1) 写)+ compact 合并 + 重启回放")
    d = tempfile.mkdtemp(prefix="idem_a_")
    path = os.path.join(d, "idem.json")
    g = IdempotencyGuard(persist_path=path, compact_every=1000)
    g.mark("k1"); g.mark("k2"); g.mark("k3")
    log_path = path + ".log"
    check(os.path.exists(log_path), "mark 产生追加日志")
    with open(log_path, encoding="utf-8") as f:
        lines = [l for l in f if l.strip()]
    check(len(lines) == 3, f"3 次 mark = 3 行日志(实为 {len(lines)}),非全量写")
    check(not os.path.exists(path), "未达阈值不写全量快照")

    d2 = tempfile.mkdtemp(prefix="idem_b_")
    path2 = os.path.join(d2, "idem.json")
    g2 = IdempotencyGuard(persist_path=path2, compact_every=5)
    for i in range(5):
        g2.mark(f"k{i}")
    check(not os.path.exists(path2 + ".log")
          and not os.path.exists(path2 + ".log.rot"),
          "compact 后追加日志清空(轮转本已删,新日志未生)")
    with open(path2, encoding="utf-8") as f:
        data = json.load(f)
    check(len(data) == 5, f"快照含全部 5 条(实为 {len(data)})")
    g2.mark("k5")
    g2b = IdempotencyGuard(persist_path=path2, compact_every=10000)
    check(all(g2b.seen(f"k{i}") for i in range(6)),
          "compact 后继续 mark:重启回放 = 快照5 + 新日志1 = 6 全可见")

    d3 = tempfile.mkdtemp(prefix="idem_c_")
    path3 = os.path.join(d3, "idem.json")
    g3 = IdempotencyGuard(persist_path=path3, compact_every=10000)
    g3.mark("k1"); g3.mark("k2"); g3.update_result("k1", {"r": 1})
    g4 = IdempotencyGuard(persist_path=path3)
    check(g4.seen("k1") and g4.seen("k2"), "重启后日志回放:k1/k2 仍可见")
    check(g4.get_result("k1") == {"r": 1}, "update 事件回放:结果已回填")


def test_idem_compat_and_eviction():
    print("\n【6】P2-3:旧快照兼容 + 容量淘汰发 delete(重启不复活)")
    d = tempfile.mkdtemp(prefix="idem_d_")
    path = os.path.join(d, "idem.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"legacy_k": {"created": time.time(), "result": {"x": 1}}}, f)
    g = IdempotencyGuard(persist_path=path)
    check(g.seen("legacy_k") and g.get_result("legacy_k") == {"x": 1},
          "旧版纯快照格式兼容(无日志,回放为空)")

    d2 = tempfile.mkdtemp(prefix="idem_e_")
    path2 = os.path.join(d2, "idem.json")
    g2 = IdempotencyGuard(persist_path=path2, max_size=2, compact_every=10000)
    g2.mark("k1"); time.sleep(0.01); g2.mark("k2"); time.sleep(0.01); g2.mark("k3")
    check(not g2.seen("k1"), "容量=2:k1 被淘汰出内存")
    g3 = IdempotencyGuard(persist_path=path2, max_size=2, compact_every=10000)
    check(not g3.seen("k1") and g3.seen("k2") and g3.seen("k3"),
          "重启回放后 k1 不复活(delete 日志生效),k2/k3 在")


def test_idem_persist_failure():
    print("\n【7】P2-3:持久化失败发事件 + stats 计数(不静默)")
    spine = _Spine()
    d = tempfile.mkdtemp(prefix="idem_f_")
    path = os.path.join(d, "idem.json")
    g = IdempotencyGuard(persist_path=path, spine=spine)
    orig_open = builtins.open

    def _bad_open(*a, **kw):
        if ".log" in str(a[0]):
            raise OSError("disk full")
        return orig_open(*a, **kw)
    builtins.open = _bad_open
    try:
        g.mark("k1")
    finally:
        builtins.open = orig_open
    check(g.seen("k1"), "持久化失败不影响内存主流程")
    check(g.stats()["persist_failures"] >= 1, "stats() 暴露 persist_failures 计数")
    check(any(e[0] == "idempotency.persist_failed" for e in spine.events),
          "发 idempotency.persist_failed 事件")
    s = IdempotencyGuard().stats()
    check("persist_failures" in s and "ops_since_compact" in s,
          "stats() 含新字段(无 persist_path 也一样)")


from foxclaw.core.recovery import RestartRecovery, RecoveryPlan


def test_recovery_plan():
    print("\n【8】P2-4:plan_recovery 分类 + recovery.plan 事件")
    spine = _Spine()
    d = tempfile.mkdtemp(prefix="recv_a_")
    log = os.path.join(d, "log.jsonl")
    r1 = RestartRecovery(log, boot_id="boot1")
    r1.task_started("t1", "可重试任务", retryable=True)
    r1.task_started("t2", "渠道任务", channel="telegram")
    r1.task_started("t3", "无标记任务")
    r2 = RestartRecovery(log, boot_id="boot2", spine=spine)
    plan_obj = r2.plan_recovery()
    check(isinstance(plan_obj, RecoveryPlan), "返回 RecoveryPlan")
    check("t1" in plan_obj.retryable, "retryable=True → retryable")
    check("t2" in plan_obj.notify_user, "channel 非空 → notify_user")
    check("t3" in plan_obj.needs_manual, "无标记 → needs_manual(保守)")
    check(len(plan_obj.details) == 3
          and {d["action"] for d in plan_obj.details} == {"retry", "notify_user", "manual"},
          "details 逐任务带 action")
    evts = [e for e in spine.events if e[0] == "recovery.plan"]
    check(len(evts) == 3, f"每个分类决定发 recovery.plan 事件(实为 {len(evts)})")
    check(isinstance(r2.scan_interrupted(), list), "scan_interrupted 仍返回 list(兼容)")


def test_recovery_details():
    print("\n【9】P2-4:start_count / compact max(started,finished) / fsync 开关")
    d = tempfile.mkdtemp(prefix="recv_b_")
    log = os.path.join(d, "log.jsonl")
    r = RestartRecovery(log, boot_id="boot1")
    r.task_started("t1", "first")
    r.task_finished("t1", status="done")
    r.task_started("t1", "second")
    records = r._replay()
    check(records["t1"].start_count == 2, "同一 task_id 两次 start → start_count=2")
    check(records["t1"].status == "in_progress" and records["t1"].description == "second",
          "重复 start 保留最后一次")

    d2 = tempfile.mkdtemp(prefix="recv_c_")
    log2 = os.path.join(d2, "log.jsonl")
    old = time.time() - 30 * 86400
    with open(log2, "w", encoding="utf-8") as f:
        f.write(json.dumps({"event": "start", "task_id": "t_old", "description": "x",
                            "pid": 1, "started": old, "boot_id": "b0"}) + "\n")
        f.write(json.dumps({"event": "finish", "task_id": "t_old", "status": "done",
                            "pid": 1, "finished": time.time()}) + "\n")
    r2 = RestartRecovery(log2, boot_id="boot1")
    r2.compact_log(keep_recent_days=7)
    check("t_old" in r2._replay(),
          "compact 用 max(started,finished):started 30 天前但刚完成的记录不丢")
    check(r2._replay()["t_old"].finished > 0, "compact 保留 finished 时间戳")

    d3 = tempfile.mkdtemp(prefix="recv_d_")
    log3 = os.path.join(d3, "log.jsonl")
    r3 = RestartRecovery(log3, boot_id="boot1", fsync=False)
    r3.task_started("t1", "x")
    r3.task_finished("t1")
    check(r3._replay()["t1"].status == "done", "fsync=False 正常读写(开关兼容)")
    r4 = RestartRecovery(os.path.join(d3, "empty.jsonl"), boot_id="b")
    p4 = r4.plan_recovery([])
    check(p4.details == [] and p4.retryable == [], "无中断 → 空计划(不炸)")


def test_api_ahs_run():
    print("\n【10】P3-2:/api/ahs/run 走 run_async + 暴露 pending 字段")
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"
    os.environ.setdefault("FOXCLAW_EMBEDDING_MODEL", "foxclaw-nonexistent-fast")
    os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_ahsrun_")
    os.environ["FOXCLAW_SKIP_DOTENV"] = "1"
    for _k in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
               "GOOGLE_API_KEY", "DASHSCOPE_API_KEY", "FOXCLAW_API_KEY"):
        os.environ[_k] = ""
    from fastapi.testclient import TestClient
    from foxclaw.api import app
    with TestClient(app) as c:
        r = c.post("/api/ahs/run", json={"task": "计算 12*12"})
        check(r.status_code == 200, f"端点 200(实为 {r.status_code})")
        body = r.json()
        check("is_pending" in body and "pending_confirmation" in body,
              "响应暴露 is_pending/pending_confirmation 字段")
        check(body.get("success") in (True, False), "success 字段正常返回")
        r2 = c.post("/api/ahs/run", json={
            "task": "计算 3*3", "confirmed": True,
            "resume_state": {"completed_steps": [], "resume_at": None}})
        check(r2.status_code == 200 and "summary" in r2.json(),
              f"resume_state 参数兼容(实为 {r2.status_code}: {r2.text[:120]})")


def main():
    print("=" * 60)
    print("批次 6 · P2 层(idempotency/recovery/compose/run_async)+ P3-2 测试")
    print("=" * 60)
    test_compose_pending()
    test_compose_failed_still_rolls_back()
    test_compose_resume()
    test_ahs_pipeline_pending()
    test_run_async()
    test_idem_append_log()
    test_idem_compat_and_eviction()
    test_idem_persist_failure()
    test_recovery_plan()
    test_recovery_details()
    test_api_ahs_run()
    print("\n" + "=" * 60)
    if FAILS:
        print(f"✗ {len(FAILS)} 项断言失败:")
        for f in FAILS:
            print(f"  - {f}")
        sys.exit(1)
    print("✓ 批次 6 全部断言通过")
    print("=" * 60)


if __name__ == "__main__":
    main()
