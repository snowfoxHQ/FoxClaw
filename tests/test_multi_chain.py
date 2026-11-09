"""智能体层深化:多步跨 Agent 由「排队跑」升级为「真链式」——后一步能看到前序步骤结果。

原缺口:_dispatch_multi 每步都只拿到同一份原始 context,等于 N 个互不相干的任务顺序执行
(「查本月餐饮花了多少,再据此定预算」的第 2 步拿不到第 1 步的数字)。
"""
import os, sys, asyncio, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_multi_chain():
    os.environ["HKC_EMBEDDING"] = "stub"
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"
    os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_chain_")

    from foxclaw.agents.agents import _with_prior, PRIOR_MAX_STEPS, PRIOR_MAX_CHARS

    print("=" * 60); print("多步跨 Agent 链式协作验证"); print("=" * 60)

    assert _with_prior("查餐饮支出", []) == "查餐饮支出"
    print("[1] 首步无前序上下文(原样)OK")

    t = _with_prior("据此定预算", [{"step": 1, "agent": "finance", "output": "餐饮 1200 元"}])
    assert "据此定预算" in t and "餐饮 1200 元" in t and "第1步(finance)" in t, t
    print("[2] 前序结果并进下一步任务文本 OK")

    many = [{"step": i, "agent": "a", "output": f"out{i}"} for i in range(1, 9)]
    t3 = _with_prior("x", many)
    assert "out8" in t3 and "out6" in t3 and "out5" not in t3, t3
    assert t3.count("第") - 1 == PRIOR_MAX_STEPS or t3.count("步(") == PRIOR_MAX_STEPS, t3
    print(f"[3] 只带最近 {PRIOR_MAX_STEPS} 步(更早的不注入)OK")

    t4 = _with_prior("x", [{"step": 1, "agent": "a", "output": "y" * 900}])
    assert "y" * PRIOR_MAX_CHARS in t4 and "y" * (PRIOR_MAX_CHARS + 1) not in t4
    print(f"[4] 单步输出截断到 {PRIOR_MAX_CHARS} 字符 OK")

    from foxclaw import Foxclaw

    os_ = Foxclaw(data_dir=os.environ["FOXCLAW_DATA_DIR"])
    sup = os_.supervisor


    class StubAgent:
        """记录真实收到的 task 文本与 context。"""
        def __init__(self, name, reply): self.name = name; self.reply = reply; self.seen = []
        async def run_task(self, task, context=None):
            self.seen.append({"task": task, "context": context or {}})
            return {"result": self.reply}


    fin = StubAgent("finance", "本月餐饮 1200 元")
    adm = StubAgent("admin", "已按 1200 元安排下周预算")
    subs = {"finance": fin, "admin": adm}
    sched = sup._schedule_for("t", subs) if hasattr(sup, "_schedule_for") else None
    if sched is None:
        from foxclaw.core.scheduler import SystemContext
        sched = sup._scheduler.schedule("t", SystemContext(active_agents=0, queue_depth=0, system_load=0.1))

    routes = [("查本月餐饮支出", "finance"), ("据此安排下周预算", "admin")]
    out = asyncio.run(sup._dispatch_multi("查餐饮并定预算", routes, sched, "sess1", subs, {}, []))

    s1, s2 = fin.seen[0]["task"], adm.seen[0]["task"]
    assert "前序步骤结果" not in s1, f"首步不该有前序前言: {s1}"
    assert "本月餐饮 1200 元" in s2, f"第2步应看到第1步结果,实际: {s2}"
    assert "据此安排下周预算" in s2
    print("[5] 第2步真实收到第1步输出(链式成立)OK")

    ps = adm.seen[0]["context"].get("prior_steps")
    assert ps and ps[0]["agent"] == "finance" and "1200" in ps[0]["output"], ps
    assert fin.seen[0]["context"].get("prior_steps") == [], "首步 prior 应为空"
    print("[6] context.prior_steps 结构化传递 OK")

    steps = out["result"]["multi_steps"]
    assert steps[1]["subtask"] == "据此安排下周预算", steps[1]["subtask"]
    assert "前序步骤结果" not in steps[1]["subtask"]
    assert out["result"]["dispatched_to"] == "multi" and len(steps) == 2
    print("[7] UI 展示的 subtask 仍是原始文本(未被污染)OK")

    big = StubAgent("finance", "X" * 5000)
    subs2 = {"finance": big, "admin": StubAgent("admin", "ok")}
    asyncio.run(sup._dispatch_multi("t", [("查", "finance"), ("用", "admin")],
                                    sched, "s2", subs2, {}, []))
    ps = subs2["admin"].seen[0]["context"]["prior_steps"]
    assert len(ps[0]["output"]) <= PRIOR_MAX_CHARS, f"入链输出未截断: {len(ps[0]['output'])}"
    assert len(subs2["admin"].seen[0]["task"]) < 2000, "任务文本不该被前序大输出撑爆"
    print(f"[8] 大输出入链自动截断({len(ps[0]['output'])} ≤ {PRIOR_MAX_CHARS} 字符)OK")

    print("\n多步跨 Agent 链式协作验证全部通过")

if __name__ == "__main__":
    test_multi_chain()
