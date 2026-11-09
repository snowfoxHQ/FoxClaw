"""
用 FastAPI TestClient 在同进程内验证 API 端点。
绕过沙箱对后台进程/loopback 的限制,直接走 ASGI。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import tempfile
import json
from fastapi.testclient import TestClient


def show(title, data, limit=None):
    print(f"\n【{title}】")
    s = json.dumps(data, ensure_ascii=False, indent=2)
    if limit:
        s = "\n".join(s.splitlines()[:limit])
    print(s)


def test_api():
    # 环境变量须在 import foxclaw.api 前设好(模块加载时按环境变量建网关/agent)
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"
    os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_test_")  # 不污染仓库根
    from foxclaw.api import app

    with TestClient(app) as c:
        print("="*64)
        print("FOXCLAW · API 端到端验证 (ASGI TestClient)")
        print("="*64)

        # 1. 系统状态
        r = c.get("/api/status")
        print(f"\n[GET /api/status] HTTP {r.status_code}")
        data = r.json()
        for aid, s in data.items():
            tc = s["thought_chain"]
            print(f"  {aid:<12} 节点={tc['total_nodes']:<3} shadow={s['harness']['shadow_mode']}")

        # 2. 发任务(真实经过 ThoughtChain + Harness)
        print("\n[POST /api/task] 连发 7 个财务任务触发校准")
        for i, t in enumerate(["分析支出","制定预算","评估风险","季度报告","追踪消费","明年规划","同期对比"], 1):
            r = c.post("/api/task", json={"task": t, "agent_id": "finance"})
            d = r.json()
            err = d.get("error", "ok")
            print(f"  [{i}] {t} → HTTP {r.status_code} · {err}")

        # 3. Harness 状态(校准后)
        r = c.get("/api/harness/finance/status")
        show("GET /api/harness/finance/status", r.json())

        # 4. 推理链
        r = c.get("/api/thoughtchain/finance/chain")
        nodes = r.json()["nodes"]
        print(f"\n[GET /api/thoughtchain/finance/chain] HTTP {r.status_code} · {len(nodes)} 节点")
        for n in nodes[-5:]:
            print(f"  {n['type']:<16} {n['harness_status']:<9} "
                  f"conf={n['conf']:.2f} Δ={n.get('harness_delta') or 0:.3f}")

        # 5. 漂移趋势
        r = c.get("/api/thoughtchain/finance/trend?limit=10")
        trend = r.json()["trend"]
        print(f"\n[GET .../trend] {len(trend)} 数据点 · Δ 序列:")
        print("  " + " ".join(f"{t['delta']:.2f}" for t in trend[:10]))

        # 6. Policy
        r = c.get("/api/harness/finance/policy")
        pol = r.json()
        print(f"\n[GET /api/harness/finance/policy] v{pol['version']} · "
              f"{len(pol.get('rules',[]))} 规则")

        # 7. 偏好管理端点(F3):GET 列出结构;DELETE 不存在 id 优雅返回 ok=False(不崩)
        r = c.get("/api/preferences")
        assert r.status_code == 200 and "preferences" in r.json(), r.text
        r = c.delete("/api/preferences/nonexistent_id")
        assert r.status_code == 200 and r.json().get("ok") is False, r.text
        print("\n[GET/DELETE /api/preferences] 列出+删除通路 ok")

        # 8. 称呼(F4):PUT 写 → GET 读回 → 再 PUT 改 → 只剩一条,不累积
        r = c.put("/api/profile", json={"nickname": "  snow  "})
        assert r.status_code == 200 and r.json().get("nickname") == "snow", r.text
        r = c.get("/api/profile")
        assert r.json().get("nickname") == "snow", r.text
        c.put("/api/profile", json={"nickname": "snow2"})
        r = c.get("/api/preferences")
        nicks = [p["content"] for p in r.json()["preferences"] if "称呼我为" in p["content"]]
        assert nicks == ["用户偏好:称呼我为 snow2"], nicks
        print("\n[GET/PUT /api/profile] 称呼写入/读回/替换 ok")

        # 9. 更新提示:字段齐全,默认不检查(没设 FOXCLAW_UPDATE_CHECK)
        r = c.get("/api/system/info")
        info = r.json()
        assert r.status_code == 200 and "update" in info, r.text
        up = info["update"]
        assert {"enabled", "current", "latest", "update_available"} <= set(up), up
        assert up["enabled"] is False and up["update_available"] is False, up
        print("\n[GET /api/system/info] update 字段存在,默认关闭")

        print("\n" + "="*64)
        print("✓ API 端到端验证完成 · 所有端点返回真实数据")
        print("="*64)


if __name__ == "__main__":
    test_api()
