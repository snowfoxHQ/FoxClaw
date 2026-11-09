"""
A 阶段联调测试 — 验证前后端实时链路打通。
重点:WebSocket 能否真实收到 run_task 触发的 harness 事件
(这是之前跨线程 create_task 失败的修复验证)。
"""
import sys, os, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["FOXCLAW_SHADOW_ROUNDS"] = "3"   # 快速进入 active 模式以触发 BLOCK
import tempfile
os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_test_")  # 不污染仓库根

# ── 测试隔离(补齐,仿 tests/run_all.py 的第二道防线)────────────────────
# 病根:本文件直接运行时不经 run_all.py,会加载项目根的真实 .env。若其中
# FOXCLAW_API_KEY 已设置,api.py 的鉴权中间件会对所有 /api/task 返回 **401**,
# 任务根本没执行 → harness 一次都不跑 → 本文件断言的"事件总线实时工作"
# 恒为 0 条。此前该失败被误判为「embedding 降级导致不拦截」,实际是 401。
# 证据:日志里每条 POST /api/task 都是 "HTTP/1.1 401 Unauthorized"。
# run_all.py 已在 env dict 里清空 FOXCLAW_API_KEY 并设 FOXCLAW_SKIP_DOTENV=1,
# 但单独运行本文件时那套不生效,故此处显式声明。
os.environ["FOXCLAW_SKIP_DOTENV"] = "1"
os.environ["FOXCLAW_API_KEY"] = ""            # 置空 = 未设鉴权(与 run_all.py 一致)
for _k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
           "GOOGLE_API_KEY", "DASHSCOPE_API_KEY"):
    os.environ[_k] = ""                      # 强制 stub,回归不联网、不产生费用

from fastapi.testclient import TestClient
from foxclaw.api import app


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


def main():
    print("=" * 60)
    print("A 阶段 · 前后端实时链路联调")
    print("=" * 60)

    with TestClient(app) as client:
        # 1. 根路径返回前端
        print("\n【1】静态前端服务")
        r = client.get("/")
        print(f"  GET / → HTTP {r.status_code} · 含前端内容: {'✓' if 'FOXCLAW' in r.text.upper() else '✗'}")
        check(r.status_code == 200 and "FOXCLAW" in r.text.upper(),
              f"GET / 返回 200 且含前端内容(HTTP {r.status_code})")

        # 2. 验证 harness 事件实时触发(直接挂监听到 OS 的 agent 总线)
        print("\n【2】实时事件触发(核心验证)")
        import foxclaw.api as api_mod
        os_inst = api_mod.os_instance()
        captured = []
        for agent in os_inst._agents.values():
            for ev in ["harness_block", "harness_warn", "harness_pass",
                       "policy_updated", "shadow_calibrated"]:
                agent.harness.bus.on(ev, lambda d, e=ev: captured.append({"event": e, "data": d}))
        for t in ["分析支出", "制定预算", "评估风险", "季度报告", "追踪消费"]:
            client.post("/api/task", json={"task": t, "agent_id": "finance"})
        time.sleep(0.2)
        ev_types = {}
        for m in captured:
            e = m["event"]
            ev_types[e] = ev_types.get(e, 0) + 1
        print(f"  实时捕获 {len(captured)} 条 harness 事件")
        for e, c in ev_types.items():
            print(f"    · {e}: {c} 条")
        if captured:
            print(f"  样例: {captured[0]['event']} · agent={captured[0]['data'].get('agent_id','?')}")
        # 这是本文件 docstring 的核心命题:"任务触发的 harness 事件能否被即时捕获"。
        # 此前它只在有事件时打印一句 ✓、无事件时打印"⚠ 未捕获"然后照常通过 —— 等于不验。
        check(len(captured) > 0,
              f"事件总线实时工作:任务触发的 harness 事件被即时捕获({len(captured)} 条)")
        # BLOCK 断言按嵌入模式分述(B 类修复):
        #   · transformer 模式:语义维度可用,跑题应真的产生 BLOCK —— 这是校准生效的证据。
        #   · hash 降级模式:语义维度已**停用**(fail-open),正常任务不再被拦,
        #     故不会有 harness_block。此时该断言的是「停用是显式的」——
        #     事件仍要通、且必须能观察到 semantic_degraded 标记。
        #     硬要求 BLOCK 会让本测试在降级下恒红,掩盖真实信号。
        _emb_degraded = getattr(os_inst._agents["finance"].harness, "semantic_degraded", False)
        if _emb_degraded:
            print("  [降级模式] 语义维度已停用 → 断言 fail-open 正确性而非 BLOCK")
            check("harness_block" not in ev_types,
                  f"降级下正常任务不产生 BLOCK(实为 {sorted(ev_types)})")
            check("harness_pass" in ev_types,
                  f"降级下正常任务应放行并发出 harness_pass(实为 {sorted(ev_types)})")
        else:
            check("harness_block" in ev_types,
                  f"shadow_rounds=3 后进入 active 并真的产生 BLOCK(实为 {sorted(ev_types)})")
        # 广播队列由后台任务持续消费,qsize 是竞态值(实测常为 0),故只作观察不断言。
        print(f"  广播队列当前 qsize={api_mod._event_queue.qsize()}(由后台任务消费,竞态值,仅观察)")

        # 2b. WebSocket 握手
        print("\n【2b】WebSocket 连接握手")
        try:
            with client.websocket_connect("/ws/harness"):
                ws_ok, ws_err = True, ""
        except Exception as e:
            ws_ok, ws_err = False, str(e)
        print(f"  {'✓' if ws_ok else '✗'} WebSocket 握手{'成功,连接已建立' if ws_ok else f'失败: {ws_err}'}")
        check(ws_ok, f"WebSocket /ws/harness 握手成功{'(' + ws_err + ')' if ws_err else ''}")

        # 3. 验证任务执行后 HTTP 数据同步
        print("\n【3】HTTP 数据回流")
        s = client.get("/api/thoughtchain/finance/summary").json()
        print(f"  推理节点: {s['total_nodes']} · 截断: {s['blocked_nodes']} "
              f"· 平均Δ: {s['avg_delta']}")
        h = client.get("/api/harness/finance/status").json()
        print(f"  Harness shadow: {h['thresholds']['shadow_mode']} "
              f"· block阈值: {h['thresholds']['block']:.3f} "
              f"· policy版本: v{h['policy']['version']}")
        # 降级可见性(B 类修复):状态接口须透出嵌入模式与语义维度是否停用,
        # 否则运维只能从服务端日志猜「为什么跑题没被拦」。
        print(f"  嵌入模式: {h['thresholds'].get('embedding_mode')} "
              f"· 语义维度降级: {h['thresholds'].get('semantic_degraded')}")
        check("semantic_degraded" in h["thresholds"],
              "harness status 透出 semantic_degraded(降级可见)")
        check("embedding_mode" in h["thresholds"],
              "harness status 透出 embedding_mode(降级可见)")
        check(s["total_nodes"] > 0, f"任务执行后 ThoughtChain 有真实节点({s['total_nodes']} 个)")
        # blocked_nodes 依赖语义拦截,降级下正常任务本就不该被截断(B 类修复)。
        if getattr(os_inst._agents["finance"].harness, "semantic_degraded", False):
            print("  [降级模式] 语义维度停用 → 正常任务不被截断属预期,不硬断言 blocked_nodes")
        else:
            check(s["blocked_nodes"] > 0, f"推理链上确发生截断({s['blocked_nodes']} 次)")
        check(h["thresholds"]["shadow_mode"] is False,
              "shadow_rounds=3 后已退出 shadow 进入 active(阈值生效)")
        check(h["policy"]["version"] >= 1, f"Policy 已落版本(v{h['policy']['version']})")

    print("\n" + "=" * 60)
    print("✓ A 阶段联调完成")
    print("=" * 60)


def test_integration():
    main()


if __name__ == "__main__":
    main()
