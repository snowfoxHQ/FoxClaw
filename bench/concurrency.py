"""
M2 · 并发压测（数字产物）

回答路线图 M2 的量化问题:
  · 并行 fork-join 相对顺序,墙钟省多少?(latency)
  · 共享库(ThoughtChain/ProcessManager/SystemBus/HMR)在多线程并发写下的吞吐,
    以及 `database is locked` 计数(应为 0)。

离线:不联网、LLM 用带固定延迟的 stub。
用法:HF_HUB_OFFLINE=1 python bench/concurrency.py
"""
import asyncio
import sys
import tempfile
import threading
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import foxclaw.core.web_search as ws
ws.needs_search = lambda q: False

from foxclaw import Foxclaw
from foxclaw.core.thought_chain import ThoughtNode

SLEEP = 0.4
PAR_TASK = "同时查今天的日程、分析本月理财、总结这篇论文"
SEQ_TASK = "先查今天的日程然后分析本月理财然后总结这篇论文"


def _slow(system, messages, max_tokens=2048, agent_id=""):
    time.sleep(SLEEP)
    return "已处理"


def bench_latency(os_):
    print("\n[1] 并行 fork-join vs 顺序（每个子任务 LLM stub 固定 " f"{SLEEP*1000:.0f}ms）")
    r = asyncio.run(os_.supervisor.run_task(PAR_TASK))
    par = r["plan"]["wall_ms"]
    t0 = time.perf_counter()
    asyncio.run(os_.supervisor.run_task(SEQ_TASK))
    seq = (time.perf_counter() - t0) * 1000
    print(f"    顺序墙钟 : {seq:8.0f} ms")
    print(f"    并行墙钟 : {par:8.0f} ms")
    print(f"    加速比   : {seq/par:8.2f}×   （3 子任务跨 3 个 Agent）")


def bench_concurrency(os_, threads=12, per_thread=20):
    print(f"\n[2] 共享库并发写压测（{threads} 线程 × {per_thread} 轮）")
    tc, pm, bus, hmr = (os_.thought_chain, os_.process_manager,
                        os_.system_bus, os_.hmr)
    locked = {"n": 0}
    errors = []
    lk = threading.Lock()

    def worker(k):
        for i in range(per_thread):
            try:
                node = ThoughtNode.create(session_id=f"b{k}", agent_id="bench",
                                          type="reasoning", content=f"n{k}_{i}", conf=0.9)
                tc.add_node(node); tc.update_harness(node.id, "PASS")
                p = pm.create(f"t{k}_{i}", agent_id=f"a{k}", schedule={"priority": 1})
                pm.mark_running(p.process_id); pm.mark_completed(p.process_id)
                bus.publish("bench.evt", {"k": k, "i": i})
                hmr.ingest(f"记忆{k}_{i}", type="concept", agent_id=f"a{k}", title=f"m{k}_{i}")
            except Exception as e:
                msg = repr(e)
                if "database is locked" in msg:
                    with lk:
                        locked["n"] += 1
                with lk:
                    errors.append(msg)

    total = threads * per_thread
    t0 = time.perf_counter()
    ts = [threading.Thread(target=worker, args=(k,)) for k in range(threads)]
    for t in ts: t.start()
    for t in ts: t.join()
    dur = time.perf_counter() - t0

    with tc._conn() as c:
        nodes = c.execute("SELECT COUNT(*) FROM thought_nodes WHERE agent_id='bench'"
                          ).fetchone()[0]
    print(f"    总写入组   : {total}  （每组 = 1 审计节点 + 1 进程流转 + 1 事件 + 1 记忆）")
    print(f"    耗时       : {dur*1000:8.0f} ms   吞吐 {total/dur:8.0f} 组/s")
    print(f"    审计落库   : {nodes} / {total}   {'✓ 不丢' if nodes >= total else '✗ 丢失'}")
    print(f"    database is locked 次数 : {locked['n']}   {'✓' if locked['n']==0 else '✗'}")
    print(f"    其它异常   : {len(errors)}   {'✓ 无' if not errors else errors[:1]}")


def main():
    os_ = Foxclaw(data_dir=tempfile.mkdtemp())
    os_.router.complete = _slow
    print("=" * 60)
    print("Foxclaw · M2 并发压测")
    print("=" * 60)
    bench_latency(os_)
    bench_concurrency(os_)
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
