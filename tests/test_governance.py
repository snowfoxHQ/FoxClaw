"""
运行时治理回归测试 — 覆盖借鉴 OpenClaw 的六项能力

验证:
  1. 工具循环检测(repeat/ping_pong/no_progress + 多级阈值)
  2. 上下文压缩(渐进降级 + 标识符保留 + 无效压缩放弃)
  3. 钩子标准化(向后兼容 + 阻断权 + 非法名校验)
  4. 幂等守卫(原子查重 + 并发 + TTL + 跨重启)
  5. 重启恢复(boot_id 防 pid 复用 + 中断识别)
  6. 干预队列(租约不重复 + 过期重入队 + drain 尊重租约)
"""
import sys, os, time, threading, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


def test_loop_detection():
    print("\n【1】工具循环检测")
    from foxclaw.harness.loop_detection import ToolLoopDetector
    # repeat
    d = ToolLoopDetector(warning=3, critical=5, circuit_break=8)
    for _ in range(8):
        v = d.record("web_search", {"q": "天气"})
    check(v.stuck and v.detector == "repeat", "重复调用检测为 repeat")
    # ping_pong
    d2 = ToolLoopDetector(warning=4, critical=6, circuit_break=10)
    for i in range(6):
        v2 = d2.record("search" if i % 2 == 0 else "fetch", {"x": i % 2})
    check(v2.stuck and v2.detector == "ping_pong", "交替调用检测为 ping_pong")
    # no_progress
    d3 = ToolLoopDetector(warning=3, critical=5, circuit_break=8)
    for _ in range(5):
        v3 = d3.record("poll", {"id": "j"}, result={"status": "pending"})
    check(v3.stuck and v3.detector == "no_progress", "轮询无变化检测为 no_progress")
    # 正常不误报
    d4 = ToolLoopDetector(warning=3)
    for q in ["a", "b", "c", "d", "e"]:
        v4 = d4.record("search", {"q": q})
    check(not v4.stuck, "多样调用不误报")
    # 熔断
    check(d.should_abort(), "达阈值 should_abort")


def test_loop_per_session():
    print("\n【1b】循环检测 per-session 隔离(阶段四·步骤13,消除跨会话污染)")
    from foxclaw.skills import SkillAdapter, set_loop_session, reset_loop_session, _loop_session
    adp = SkillAdapter(None)   # _detector_for 不依赖 registry,构造最小实例即可

    # 会话 A 连续同工具 8 次(达熔断)
    tok = set_loop_session("sess_A")
    try:
        for _ in range(8):
            va = adp._detector_for(_loop_session.get()).record("web_search", {"q": "天气"})
    finally:
        reset_loop_session(tok)
    check(va.stuck, "会话 A 连续同工具达熔断")

    # 切到会话 B:历史应从零,同工具仅 1 次不应被 A 污染成 stuck
    tok = set_loop_session("sess_B")
    try:
        vb = adp._detector_for(_loop_session.get()).record("web_search", {"q": "天气"})
    finally:
        reset_loop_session(tok)
    check(not vb.stuck, "会话 B 不受会话 A 污染(独立历史)")
    check(not adp._detector_for("sess_B").should_abort(), "会话 B 未达熔断")

    # 不同会话持有独立检测器实例;无会话时用共享默认槽(向后兼容)
    check(adp._detector_for("sess_A") is not adp._detector_for("sess_B"), "不同会话独立检测器实例")
    check(adp._detector_for(None) is adp._detector_for(None), "无会话共用默认槽(稳定)")
    check(_loop_session.get() is None, "退出后 contextvar 已复位(无残留)")


def test_compaction():
    print("\n【2】上下文压缩")
    from foxclaw.core.compaction import Compactor, extract_identifiers, estimate_messages_tokens
    # token 估算(压缩触发的判据)
    check(estimate_messages_tokens([{"role": "user", "content": "你好世界"}]) > 0, "token 估算非零")
    # 标识符提取(含 ¥金额)
    ids = extract_identifiers([{"role": "user", "content": "订单 txn_abc12345 金额 ¥1,200 流水 202606210001"}])
    check("txn_abc12345" in ids and any("¥" in i for i in ids), "关键标识符提取(含金额)")
    # 截断兜底(无 router)
    msgs = [{"role": "system", "content": "你是助手"}]
    for i in range(10):
        msgs.append({"role": "user", "content": f"长消息{i}" * 20})
        msgs.append({"role": "assistant", "content": f"长回复{i}" * 20})
    c = Compactor(router=None, max_tokens=50, keep_recent=2)
    r = c.compact(msgs)
    check(r.method == "truncate" and r.compacted_tokens < r.original_tokens, "无 router 截断兜底有效")
    check(r.messages[-1] == msgs[-1], "最近消息保留")
    # 巨大系统消息 → 放弃无效压缩
    huge = [{"role": "system", "content": "x" * 8000}]
    for i in range(6):
        huge.append({"role": "user", "content": f"m{i}"})
    c2 = Compactor(router=None, max_tokens=100, keep_recent=2)
    r2 = c2.compact(huge)
    check(r2.compacted_tokens <= r2.original_tokens, "无效压缩时不会变大")


def test_hooks():
    print("\n【3】钩子标准化")
    from foxclaw.skills.lifecycle import HookManager
    from foxclaw.skills.hooks_spec import Hooks, normalize_hook, is_valid_hook
    check(normalize_hook("pre_skill") == Hooks.BEFORE_TOOL_CALL, "旧名规整为标准名")
    check(is_valid_hook("on_loop"), "on_loop 是合法钩子")
    # 向后兼容互通
    hm = HookManager()
    fired = []
    hm.register("pre_skill", lambda ctx: fired.append("x") or {})
    hm.fire("before_tool_call", "calc", {})
    check("x" in fired, "旧名注册 + 标准名触发 互通")
    # 阻断权
    hm2 = HookManager()
    hm2.register("before_tool_call", lambda ctx: {"block": True, "reason": "test"})
    check(hm2.fire("before_tool_call", "calc", {}).blocked, "before_tool_call 可阻断")
    hm3 = HookManager()
    hm3.register("after_tool_call", lambda ctx: {"block": True})
    check(not hm3.fire("after_tool_call", "calc", {}).blocked, "after_tool_call 无阻断权")
    # 非法名警告
    hm4 = HookManager()
    hm4.register("typo_hook", lambda ctx: {})
    check(any("未知钩子" in a.get("error", "") for a in hm4.audit_log), "拼错钩子名有警告")


def test_idempotency():
    print("\n【4】幂等守卫")
    from foxclaw.core.idempotency import IdempotencyGuard, make_key
    k1 = make_key("message", "telegram", "msg1", "u1")
    k2 = make_key("message", "telegram", "msg1", "u1")
    check(k1 == k2, "相同输入得相同 key")
    g = IdempotencyGuard(ttl_seconds=3600)
    check(g.check_and_mark(k1) and not g.check_and_mark(k1), "原子查重:首次True重复False")
    # 并发只1个成功
    g2 = IdempotencyGuard(ttl_seconds=3600)
    results, lock = [], threading.Lock()
    def worker():
        r = g2.check_and_mark("contended")
        with lock: results.append(r)
    ts = [threading.Thread(target=worker) for _ in range(50)]
    for t in ts: t.start()
    for t in ts: t.join()
    check(sum(results) == 1, "50 线程抢同 key 恰好 1 个成功")
    # TTL
    g3 = IdempotencyGuard(ttl_seconds=0.3)
    g3.check_and_mark("tmp"); time.sleep(0.4)
    check(g3.check_and_mark("tmp"), "TTL 过期后可重新执行")
    # 跨重启
    p = tempfile.mktemp(suffix=".json")
    ga = IdempotencyGuard(persist_path=p); ga.check_and_mark("pk")
    gb = IdempotencyGuard(persist_path=p)
    check(gb.seen("pk"), "持久化跨重启")
    os.remove(p) if os.path.exists(p) else None


def test_recovery():
    print("\n【5】重启恢复")
    from foxclaw.core.recovery import RestartRecovery, _pid_alive
    log = tempfile.mktemp(suffix=".jsonl")
    # boot_id 防 pid 复用
    rA = RestartRecovery(log_path=log, boot_id="bootA")
    rA.task_started("t1", "中断任务")
    # 即使 pid 是存活的(复用),boot_id 不同也应识别中断
    rB = RestartRecovery(log_path=log, boot_id="bootB", current_pid=os.getpid())
    interrupted = rB.scan_interrupted()
    check(len(interrupted) == 1 and interrupted[0].task_id == "t1", "boot_id 识别中断(防 pid 复用)")
    # 本次启动自己的任务不误判
    rB.task_started("t2", "本次任务")
    check(not any(t.task_id == "t2" for t in rB.scan_interrupted()), "不误判本次启动的任务")
    # 完成的不算中断
    rB.task_finished("t2")
    check(not any(t.task_id == "t2" for t in rB.scan_interrupted()), "已完成任务不算中断")
    check(_pid_alive(os.getpid()) and not _pid_alive(999999), "pid 存活检测准确")
    # 危险路径回归:探测【外部活进程】必须 True 且【绝不杀死】它
    # (旧实现在 Windows 上 os.kill(pid,0) 会 TerminateProcess 误杀,本用例专抓此坑)
    import subprocess
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    try:
        alive = _pid_alive(proc.pid)
        time.sleep(0.4)
        not_killed = proc.poll() is None
        check(alive and not_killed, "探测外部活进程:判活且非破坏性(不误杀)")
    finally:
        proc.kill()
    os.remove(log) if os.path.exists(log) else None


def test_steering_queue():
    print("\n【6】干预队列")
    from foxclaw.core.steering_queue import SteeringQueue
    # 基本流
    q = SteeringQueue(lease_seconds=30)
    q.publish("m1", "finance", {"r": "A"})
    leased = q.lease("m1")
    check(len(leased) == 1, "publish→lease")
    check(q.ack(leased[0].item_id, leased[0].lease_id), "ack 成功移除")
    # 并发不重复租用
    q2 = SteeringQueue(lease_seconds=30)
    for i in range(20):
        q2.publish("m2", "a", {"i": i})
    got, lock = [], threading.Lock()
    def consumer():
        items = q2.lease("m2", max_items=20)
        with lock: got.extend(it.item_id for it in items)
    ts = [threading.Thread(target=consumer) for _ in range(5)]
    for t in ts: t.start()
    for t in ts: t.join()
    check(len(got) == len(set(got)) == 20, "并发租取不重复")
    # 租约过期重入队
    q3 = SteeringQueue(lease_seconds=0.3)
    q3.publish("m3", "a", {}); q3.lease("m3"); time.sleep(0.4)
    check(q3.pending_count() == 1, "租约过期自动重入队(不丢)")
    # drain 尊重租约
    q4 = SteeringQueue(lease_seconds=30)
    q4.publish("m4", "a", {"r": 1}); q4.publish("m4", "a", {"r": 2})
    q4.lease("m4", max_items=1)
    check(len(q4.drain("m4")) == 1, "drain 跳过被租用项")


def test_token_meter():
    print("\n【7】Token 计量器")
    from foxclaw.core.token_meter import TokenMeter, estimate_tokens
    # 估算非零
    check(estimate_tokens("你好世界hello") > 0, "token 估算非零")
    check(estimate_tokens("") == 0, "空文本 0 token")
    m = TokenMeter()
    # 真实 usage 优先(estimated=False)
    r1 = m.measure_call(system="s", messages=[{"role": "user", "content": "q"}],
                        output="a", provider="deepseek", model="deepseek-chat",
                        agent_id="finance", usage={"input_tokens": 100, "output_tokens": 200})
    check(not r1.estimated and r1.total_tokens == 300, "真实 usage 优先,total 正确")
    # 无 usage 走估算(estimated=True)
    r2 = m.measure_call(system="你是主管", messages=[{"role": "user", "content": "分析理财预算"}],
                        output="已完成分析", provider="deepseek", model="deepseek-chat",
                        agent_id="supervisor", usage=None)
    check(r2.estimated and r2.total_tokens > 0, "无 usage 走估算")
    # 按 agent 聚合(agent_id 正确)
    agg = m.by_agent()
    check("finance" in agg and "supervisor" in agg, "按 Agent 聚合(非 unknown)")
    check(agg["finance"]["total_tokens"] == 300, "Agent 聚合数值正确")
    # 报告不崩
    check(len(m.report()) > 0, "文本报告生成")
    # 成本估算
    cost = m.estimate_cost(price_per_1k_input=0.001, price_per_1k_output=0.002)
    check(cost["estimated_cost"] >= 0, "成本估算")


def test_thoughtchain_concurrency():
    print("\n【8】ThoughtChain 并发写(共享审计存储)")
    from foxclaw.core.thought_chain import ThoughtChain, ThoughtNode
    tc = ThoughtChain(db_path=tempfile.mktemp(suffix=".db"))
    # WAL 已启用?(并发加固的核心)
    with tc._conn() as c:
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
    check(mode.lower() == "wal", f"WAL 日志模式已启用(实为 {mode})")
    # N 线程 × M 节点并发写:默认 busy_timeout=0 时这会 database is locked
    N, M, errors = 8, 25, []
    def writer(aid):
        try:
            for i in range(M):
                node = ThoughtNode.create(session_id=f"s{aid}", agent_id=f"a{aid}",
                                          type="reasoning", content=f"n{i}", conf=0.9)
                tc.add_node(node)
                tc.update_harness(node.id, "PASS")
        except Exception as e:
            errors.append(repr(e))
    ts = [threading.Thread(target=writer, args=(k,)) for k in range(N)]
    for t in ts: t.start()
    for t in ts: t.join()
    check(not errors, f"{N} 线程并发写无异常(否则: {errors[:2]})")
    with tc._conn() as c:
        total = c.execute("SELECT COUNT(*) FROM thought_nodes").fetchone()[0]
    check(total == N * M, f"并发写不丢:{total}/{N*M} 条落库")


def test_harness_action_wiring():
    print("\n【9】Harness 学习规则 action 级生效(penalize_tool / restrict_scope)")
    from foxclaw.core.thought_chain import ThoughtChain, ThoughtNode
    from foxclaw.harness.harness import Harness
    tc = ThoughtChain(db_path=tempfile.mktemp(suffix=".db"))

    def fresh(baseline_entities):
        h = Harness(agent_id="t", thought_chain=tc,
                    policy_path=tempfile.mktemp(suffix=".yaml"), shadow_rounds=0)
        h.register_goal("分析财务预算", allowed_tool_categories={"read", "finance"},
                        baseline_entities=baseline_entities)
        h._shadow_mode = False          # 关观测期,直接进入实判
        h._block_threshold, h._warn_threshold = 0.25, 0.18
        return h

    def node(tool=None, entities=None):
        n = ThoughtNode.create(session_id="s", agent_id="t", type="tool_intent",
                               content="继续分析预算", conf=0.9,
                               tool_name=tool, entities=entities or [])
        tc.add_node(n)
        return n

    # —— penalize_tool:get_balance 本在 finance 允许集,基线判 0.0 ——
    h1 = fresh({"预算", "收支"})
    check(h1.check(node(tool="get_balance")).scores["tool"] == 0.0,
          "基线:允许的工具 get_balance 工具分 = 0.0")
    # 注入一条 penalize_tool 学习规则(模拟之前 BLOCK 学到的)→ 应立即改变判定
    h1._policy.setdefault("rules", []).append({
        "id": "r1", "source": "harness_learned",
        "action": {"type": "penalize_tool", "tool_pattern": "get_balance"}})
    h1._reindex_learned_actions()
    r1 = h1.check(node(tool="get_balance"))
    check(r1.scores["tool"] == 1.0 and r1.is_blocked,
          "penalize_tool 生效:同一工具被抬到 1.0 并 BLOCK")

    # —— restrict_scope:大基线下加 2 个新实体,基线分低(0.2),规则应抬到 1.0 ——
    big = {f"e{i}" for i in range(10)}
    h2 = fresh(big)
    base_scope = h2.check(node(entities=["e0", "x1", "x2"])).scores["scope"]
    check(abs(base_scope - 0.2) < 1e-6, f"基线:2 个新实体 scope≈0.2(实为 {base_scope})")
    h2._policy.setdefault("rules", []).append({
        "id": "r2", "source": "harness_learned",
        "action": {"type": "restrict_scope", "entity_expansion_limit": 1}})
    h2._reindex_learned_actions()
    r2 = h2.check(node(entities=["e0", "x1", "x2"]))
    check(r2.scores["scope"] == 1.0, "restrict_scope 生效:超上限 scope 抬到 1.0")

    # —— 端到端:一次真实 BLOCK → 【P0-1 新行为】规则进**影子**(不生效),不再立即重建索引 ——
    h3 = fresh({"预算"})
    h3.check(node(tool="delete_everything"))   # delete 类越权 → BLOCK → 影子候选
    check(not h3._is_penalized_tool("delete_everything"),
          "P0-1:BLOCK 后规则进影子,**不立即生效**(未重建索引)")
    shadow = h3._policy.get("shadow_rules", [])
    check(len(shadow) == 1 and shadow[0]["action"]["type"] == "penalize_tool"
          and shadow[0]["shadow"]["status"] == "observing",
          "P0-1:影子规则已入库且处于 observing")
    # 手工 promote 后应立即生效(升迁路径与自动升迁同构)
    h3._promote_rule(shadow[0])
    check(h3._is_penalized_tool("delete_everything"),
          "P0-1:promote 后规则生效(索引已重建)")


def test_harness_thread_safety():
    print("\n【10】Harness 累积态线程安全(为请求 offload 到线程池铺路)")
    from foxclaw.core.thought_chain import ThoughtChain, ThoughtNode
    from foxclaw.harness.harness import Harness
    tc = ThoughtChain(db_path=tempfile.mktemp(suffix=".db"))
    h = Harness(agent_id="t", thought_chain=tc,
                policy_path=tempfile.mktemp(suffix=".yaml"), shadow_rounds=10**9)
    N, M, errors = 8, 30, []

    def worker(k):
        try:
            # 每线程在自己的 context 里注册基线(contextvar 线程独立),再并发 check
            h.register_goal(f"目标{k}", allowed_tool_categories={"read"},
                            baseline_entities={"e"})
            for i in range(M):
                node = ThoughtNode.create(session_id=f"s{k}", agent_id="t",
                                          type="reasoning", content=f"n{k}_{i}", conf=0.9)
                tc.add_node(node)
                h.check(node)
        except Exception as e:
            errors.append(repr(e))

    ts = [threading.Thread(target=worker, args=(k,)) for k in range(N)]
    for t in ts: t.start()
    for t in ts: t.join()

    check(not errors, f"{N} 线程并发 check 无异常(否则: {errors[:2]})")
    # 锁保护下 _run_count 无丢增量(无锁时 += 竞态会丢)
    check(h._run_count == N * M, f"_run_count 无丢增量({h._run_count}=={N*M})")
    check(len(h._delta_history) == N * M, f"_delta_history 无丢({len(h._delta_history)}=={N*M})")


def test_harness_delta_history_bounded():
    print("\n【11】深审修复:_delta_history 校准后停止增长(长跑不无界泄漏)")
    from foxclaw.core.thought_chain import ThoughtChain, ThoughtNode
    from foxclaw.harness.harness import Harness
    tc = ThoughtChain(db_path=tempfile.mktemp(suffix=".db"))
    rounds = 5
    h = Harness(agent_id="t2", thought_chain=tc,
                policy_path=tempfile.mktemp(suffix=".yaml"), shadow_rounds=rounds)
    h.register_goal("目标", allowed_tool_categories={"read"}, baseline_entities={"e"})
    for i in range(rounds * 20):        # 远超 shadow_rounds
        node = ThoughtNode.create(session_id="s", agent_id="t2",
                                  type="reasoning", content=f"n{i}", conf=0.9)
        tc.add_node(node); h.check(node)
    # 仅 shadow 期(前 rounds 次)append,校准后不再 append → 上限=rounds,不随 run_count 无界增长
    check(len(h._delta_history) <= rounds,
          f"_delta_history 有界(≤{rounds}),实得 {len(h._delta_history)}")
    check(h._run_count == rounds * 20, "run_count 仍完整计数(每次 check 都记)")
    check(not h._shadow_mode, "shadow 已结束(已校准)")


def test_orchestration_request_log_bounded():
    print("\n【12】深审修复:orchestrator.request_log 有界(长跑不泄漏)")
    import tempfile as _tf, shutil as _sh, asyncio as _aio
    from foxclaw import Foxclaw
    from foxclaw.orchestration import CrossAgentRequest
    d = _tf.mkdtemp(prefix="reqlog_")
    try:
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        os_.finance_runtime.set_balance("default", 100000)
        for _ in range(1200):           # 远超 maxlen=1000
            os_.orchestrator.route_request(CrossAgentRequest(
                from_agent="admin", to_agent="finance", request_type="budget_request",
                payload={"amount": 10, "category": "travel", "account": "default"},
                reason="x"))
        check(len(os_.orchestrator.request_log) <= 1000,
              f"request_log 有界(≤1000),实得 {len(os_.orchestrator.request_log)}")
        _aio.run(os_.aclose())
    finally:
        _sh.rmtree(d, ignore_errors=True)


def test_hmr_adapter_stats_lock():
    print("\n【13】深审修复:HMR 适配层 stats/status 补齐锁覆盖(与 ingest/recall 互斥)")
    import threading, tempfile, time
    from foxclaw.core.memory_adapter import create_memory_backend
    hmr = create_memory_backend(storage_path=tempfile.mkdtemp(prefix="hmrlock_"))
    if not hasattr(hmr, "_lock"):
        print("  ~ MockHMR(无 vendor/hmr)无 _lock,跳过"); return
    # 白盒:主线程持锁 → 另一线程的 stats() 必须等锁释放才完成(证明 stats 已进锁)。
    # 补锁前 stats 不持锁 → 会在持锁期间直接完成,order 顺序相反。
    order = []
    hmr._lock.acquire()
    def call_stats():
        hmr.stats(); order.append("stats_done")
    t = threading.Thread(target=call_stats); t.start()
    time.sleep(0.15); order.append("released"); hmr._lock.release()
    t.join(timeout=5)
    check(order == ["released", "stats_done"],
          f"stats() 应等 _lock 释放才完成(证明补锁生效),实得 {order}")
    # get_system_status 同样补了锁
    hmr._lock.acquire()
    order2 = []
    def call_status():
        hmr.get_system_status(); order2.append("status_done")
    t2 = threading.Thread(target=call_status); t2.start()
    time.sleep(0.15); order2.append("released"); hmr._lock.release()
    t2.join(timeout=5)
    check(order2 == ["released", "status_done"], f"get_system_status 应持锁,实得 {order2}")


def main():
    print("=" * 56)
    print("运行时治理回归测试")
    print("=" * 56)
    tests = [test_loop_detection, test_compaction, test_hooks,
             test_idempotency, test_recovery, test_steering_queue,
             test_token_meter, test_thoughtchain_concurrency,
             test_harness_action_wiring, test_harness_thread_safety,
             test_harness_delta_history_bounded, test_orchestration_request_log_bounded,
             test_hmr_adapter_stats_lock]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"  ✗ {t.__name__} 失败: {e}")
            failed += 1
    print("\n" + "=" * 56)
    print(f"结果:{len(tests) - failed}/{len(tests)} 组通过")
    print("=" * 56)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
