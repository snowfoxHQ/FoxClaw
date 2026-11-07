"""
顶层任务并发隔离回归（per-task 会话状态 via contextvars）

背景:FastAPI 多个并发请求跑在同一事件循环、可能打到同一 Agent 实例。旧实现把
_session_id/_parent_node_id/harness._baseline 存在实例上,await 之后的推理节点会被
"最后启动的会话"吞掉(实测分布 [2×7,10])。改为 contextvar 承载后各任务隔离。

验证:
  1. 同一 Agent 实例并发 N 个顶层任务 → 每会话节点数均衡、无跨会话父链接、goal 归属正确;
  2. 主管并发处理不同领域任务 → 各自分诊正确、会话不串。
"""
import asyncio
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import foxclaw.core.web_search as ws
ws.needs_search = lambda q: False

from foxclaw import Foxclaw


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


def _slow(system, messages, max_tokens=2048, agent_id=""):
    time.sleep(0.1)
    return "ok"


def _build(data_dir=None):
    os_ = Foxclaw(data_dir=data_dir or tempfile.mkdtemp())
    os_.router.complete = _slow
    return os_


@pytest.fixture(scope="module")
def os_(tmp_path_factory):
    """共享同一 Foxclaw 实例(等价脚本式 main() 里 `os_ = _build()` 一次传给 2 个测试)。"""
    return _build(data_dir=str(tmp_path_factory.mktemp("toplevel_concurrency")))


def test_same_instance_concurrent(os_):
    print("\n【1】同一 Agent 实例并发 8 个顶层任务:无串味")
    office = os_.office
    N = 8
    tasks = [f"学习任务 MARK{i:02d} 请总结" for i in range(N)]

    async def run():
        return await asyncio.gather(*[office.run_task(t) for t in tasks])
    res = asyncio.run(run())

    tc = os_.thought_chain
    con = sqlite3.connect(tc.db_path)
    rows = con.execute("SELECT id,session_id,parent_ids FROM thought_nodes "
                       "WHERE agent_id='office'").fetchall()
    sid_of = {r[0]: r[1] for r in rows}
    per = {}
    for _id, sid, _p in rows:
        per[sid] = per.get(sid, 0) + 1
    cross = sum(1 for _id, sid, p in rows for par in json.loads(p)
                if par in sid_of and sid_of[par] != sid)
    # goal 归属:每个返回会话的 goal 节点含本任务标记
    ok_goal = 0
    for i, r in enumerate(res):
        g = con.execute("SELECT content FROM thought_nodes WHERE session_id=? "
                        "AND type='goal'", (r["session_id"],)).fetchall()
        if g and f"MARK{i:02d}" in g[0][0]:
            ok_goal += 1

    check(len(per) == N, f"产生 {N} 个独立会话")
    check(len(set(per.values())) == 1, f"每会话节点数均衡(无坍缩): {sorted(per.values())}")
    check(cross == 0, "无跨会话父链接(推理链未错连)")
    check(ok_goal == N, f"每个会话的 goal 归属正确: {ok_goal}/{N}")


def test_supervisor_concurrent(os_):
    print("\n【2】主管并发处理不同领域任务:分诊正确、会话不串")
    sup = os_.supervisor

    async def run():
        return await asyncio.gather(
            sup.run_task("分析本月理财预算"),      # → finance
            sup.run_task("总结这篇论文的要点"),    # → office(学习)
            sup.run_task("你能做什么"),            # 元问题 → supervisor 自答
        )
    res = asyncio.run(run())
    routed = [r["result"].get("dispatched_to") for r in res]
    sids = [r["session_id"] for r in res]
    check(routed == ["finance", "office", "supervisor"], f"三个并发任务分诊各就各位: {routed}")
    check(len(set(sids)) == 3, "三个任务会话 id 互不相同(未串会话)")


def main():
    print("=" * 56)
    print("顶层任务并发隔离回归")
    print("=" * 56)
    os_ = _build()
    for t in (test_same_instance_concurrent, test_supervisor_concurrent):
        t(os_)
    print("\n" + "=" * 56)
    print("顶层并发隔离通过")
    print("=" * 56)


if __name__ == "__main__":
    main()
