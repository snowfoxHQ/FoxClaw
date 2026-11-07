"""
微内核调度器(v2 ① Step1:Scheduler v0)测试 —— 全离线,线程级。

覆盖:
  1. 无上限(默认):准入直通,行为不变;并发不受限(Barrier 确定性达峰值 N)
  2. 有上限:并发配额生效(peak_running 不超过 max_concurrency = 背压)
  3. 生命周期事件:每任务 emit task.lifecycle.{scheduled,running,completed};失败 → failed
     (【P5-2】回退路径原用 started,与 ProcessManager 的 running 不一致,已统一为 running)
  4. 记账 + 结果透传 + 异常透传
"""
import sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.kernel import Scheduler


class _CapSpine:
    def __init__(self): self.events = []; self._lk = threading.Lock()
    def publish(self, topic, payload=None, source="", **kw):
        with self._lk: self.events.append(topic)


def _run_n_concurrent(sched, n, work):
    threads = [threading.Thread(target=lambda: sched.run(work, task="t")) for _ in range(n)]
    for t in threads: t.start()
    for t in threads: t.join()


def test_unlimited_reaches_peak():
    print("\n【1】无上限(默认):并发不受限,行为直通")
    sched = Scheduler()                         # max_concurrency=None
    N = 6
    barrier = threading.Barrier(N)              # 让 N 个任务确定性地同时在跑
    def work():
        barrier.wait(timeout=5)                 # 全部到齐才继续 → 峰值=N
        time.sleep(0.01)
        return "ok"
    _run_n_concurrent(sched, N, work)
    st = sched.stats()
    assert st["max_concurrency"] is None
    assert st["peak_running"] == N, f"无上限应达峰值 {N},实得 {st['peak_running']}"
    assert st["completed"] == N and st["submitted"] == N
    print(f"  ✓ 无上限:peak={st['peak_running']}=N,completed={st['completed']}")


def test_concurrency_cap():
    print("\n【2】有上限:并发配额生效(背压)")
    sched = Scheduler(max_concurrency=2)
    def work():
        time.sleep(0.02)
        return "ok"
    _run_n_concurrent(sched, 8, work)
    st = sched.stats()
    assert st["peak_running"] <= 2, f"并发峰值不得超上限 2,实得 {st['peak_running']}"
    assert st["peak_running"] == 2, "8 任务/上限 2 应打满到 2"
    assert st["completed"] == 8
    print(f"  ✓ 上限 2:peak={st['peak_running']}(不超上限),completed={st['completed']}")


def test_lifecycle_events_and_accounting():
    print("\n【3】生命周期事件 + 记账 + 失败传播")
    spine = _CapSpine()
    sched = Scheduler(spine=spine)
    # 成功任务
    r = sched.run(lambda: 42, agent_id="finance", task="算账")
    assert r == 42
    assert spine.events == ["task.lifecycle.scheduled", "task.lifecycle.running",
                            "task.lifecycle.completed"], spine.events
    # 失败任务:异常透传 + failed 事件 + 记账
    spine.events.clear()
    raised = False
    try:
        sched.run(lambda: (_ for _ in ()).throw(RuntimeError("boom")), task="崩")
    except RuntimeError:
        raised = True
    assert raised, "异常应透传"
    assert spine.events == ["task.lifecycle.scheduled", "task.lifecycle.running",
                            "task.lifecycle.failed"], spine.events
    st = sched.stats()
    assert st["submitted"] == 2 and st["completed"] == 1 and st["failed"] == 1
    assert st["running"] == 0, "跑完 running 归零"
    print(f"  ✓ 成功→completed事件;失败→failed事件+异常透传;记账 {st}")


def test_pm_delegation_unified_lifecycle():
    print("\n【4】Step3:有 ProcessManager → 委托它发唯一 task.lifecycle.<state>(不双套词汇)")
    from foxclaw.kernel import EventSpine, ProcessManager
    from foxclaw.core.system_bus import SystemBus
    spine = EventSpine()
    seen = []
    spine.project(lambda e: seen.append(e.topic))
    pm = ProcessManager(bus=SystemBus(spine=spine))       # pm 经总线发事件到脊柱
    sched = Scheduler(spine=spine, process_manager=pm)    # 调度器委托 pm
    r = sched.run(lambda: 7, agent_id="finance", task="t")
    assert r == 7
    life = [t for t in seen if t.startswith("task.lifecycle")]
    assert life == ["task.lifecycle.scheduled", "task.lifecycle.running",
                    "task.lifecycle.completed"], f"应由 pm 发唯一词汇,实得 {life}"
    # pm 里确有该进程(顶层任务也成了进程),调度器记账仍在
    assert pm.stats()["by_state"].get("completed") == 1
    assert sched.stats()["completed"] == 1
    print("  ✓ pm 发 task.lifecycle.{scheduled,running,completed};顶层任务纳入进程;记账双在")


def test_priority_admission():
    print("\n【5】Step5:满载后释放 → 高优先排队者先获空位(不抢占运行中)")
    sched = Scheduler(max_concurrency=1)
    order = []
    running = threading.Event(); release = threading.Event()
    def blocker():
        sched.run(lambda: (running.set(), release.wait(5))[-1], priority=0, task="blocker")
    tb = threading.Thread(target=blocker); tb.start()
    assert running.wait(5), "blocker 应占住唯一槽"
    def worker(p, name):
        sched.run(lambda: order.append(name), priority=p, task=name)
    tlo = threading.Thread(target=worker, args=(0, "lo"))
    thi = threading.Thread(target=worker, args=(10, "hi"))
    tlo.start(); time.sleep(0.05); thi.start(); time.sleep(0.15)   # 两者都已在准入队列里等
    release.set()                                                  # 释放 blocker 的槽
    for t in (tb, tlo, thi): t.join(5)
    assert order == ["hi", "lo"], f"高优先应先获空位,实得 {order}"
    print(f"  ✓ 释放后:高优先先获空位 → {order}(准入优先级,非抢占运行中)")


def main():
    print("=" * 60)
    print("微内核调度器 · Scheduler(v2 ① Step1/3/5)")
    print("=" * 60)
    test_unlimited_reaches_peak()
    test_concurrency_cap()
    test_lifecycle_events_and_accounting()
    test_pm_delegation_unified_lifecycle()
    test_priority_admission()
    print("\n" + "=" * 60)
    print("✓ Step1 验证完成:准入配额(背压)+ 生命周期事件 + 记账 + 透传")
    print("=" * 60)


if __name__ == "__main__":
    main()
