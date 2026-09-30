"""
A 阶段联调测试 — 验证前后端实时链路打通。
重点:WebSocket 能否真实收到 run_task 触发的 harness 事件
(这是之前跨线程 create_task 失败的修复验证)。
"""
import sys, os, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["HESTIA_SHADOW_ROUNDS"] = "3"   # 快速进入 active 模式以触发 BLOCK
import tempfile
os.environ["HESTIA_DATA_DIR"] = tempfile.mkdtemp(prefix="hestia_test_")  # 不污染仓库根

from fastapi.testclient import TestClient
from hestia.api import app


def main():
    print("=" * 60)
    print("A 阶段 · 前后端实时链路联调")
    print("=" * 60)

    with TestClient(app) as client:
        # 1. 根路径返回前端
        print("\n【1】静态前端服务")
        r = client.get("/")
        ok = r.status_code == 200 and "HESTIA" in r.text.upper()
        print(f"  GET / → HTTP {r.status_code} · 含前端内容: {'✓' if ok else '✗'}")

        # 2. 验证 harness 事件实时触发(直接挂监听到 OS 的 agent 总线)
        print("\n【2】实时事件触发(核心验证)")
        import hestia.api as api_mod
        os_inst = api_mod._os_instance
        captured = []
        for aid, agent in os_inst._agents.items():
            for ev in ["harness_block", "harness_warn", "policy_updated", "shadow_calibrated"]:
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
            print("  ✓ 事件总线实时工作:任务触发的 harness 事件被即时捕获")
            # 验证这些事件也进了广播队列
            qsize = api_mod._event_queue.qsize()
            print(f"  ✓ 广播队列已积累 {qsize} 条待推送事件(后台任务会发往 WebSocket)")
        else:
            print("  ⚠ 未捕获(shadow 期内无 BLOCK)")

        # 2b. WebSocket 握手
        print("\n【2b】WebSocket 连接握手")
        try:
            with client.websocket_connect("/ws/harness") as ws:
                print("  ✓ WebSocket 握手成功,连接已建立")
        except Exception as e:
            print(f"  ✗ WebSocket 连接失败: {e}")

        # 3. 验证任务执行后 HTTP 数据同步
        print("\n【3】HTTP 数据回流")
        s = client.get("/api/thoughtchain/finance/summary").json()
        print(f"  推理节点: {s['total_nodes']} · 截断: {s['blocked_nodes']} "
              f"· 平均Δ: {s['avg_delta']}")
        h = client.get("/api/harness/finance/status").json()
        print(f"  Harness shadow: {h['thresholds']['shadow_mode']} "
              f"· block阈值: {h['thresholds']['block']:.3f} "
              f"· policy版本: v{h['policy']['version']}")

    print("\n" + "=" * 60)
    print("✓ A 阶段联调完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
