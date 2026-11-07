"""D1:/api/task/stream SSE —— 任务执行流式化(逐 stage 进度 + act 逐 token + 最终 result)。
与 test_streaming.py 的 /api/chat/stream 端点测试对称:那条测对话直答流式,这条测**走完整认知
管线**的任务流式。核心逻辑(run_task_stream)在 test_agent_lifecycle 已直测,这里补端点级 SSE 打通。"""
import os, sys, tempfile, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_task_stream():
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"
    os.environ.setdefault("FOXCLAW_EMBEDDING_MODEL", "foxclaw-nonexistent-fast")
    os.environ["FOXCLAW_STREAM_RATE"] = "60"   # 放开限流(限流由 test_streaming 覆盖),这里只验通路
    os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_taskstream_")
    # 强制 stub(离线/确定性/免费):关掉 .env,使进程环境变量成为唯一来源。
    # (旧写法是逐个把 key 置空以依赖 load_dotenv(override=False)——但那挡不住「先 pop 再
    #  import」的路径,也挡不住 .env 里的 FOXCLAW_MCP_SERVER 等非 key 变量。见 api.py 说明。)
    os.environ["FOXCLAW_SKIP_DOTENV"] = "1"
    for _k in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
               "GOOGLE_API_KEY", "DASHSCOPE_API_KEY", "FOXCLAW_API_KEY"):
        os.environ[_k] = ""

    print("=" * 60); print("D1 · /api/task/stream 任务流式化验证"); print("=" * 60)

    from fastapi.testclient import TestClient
    import foxclaw.api as _api
    from foxclaw.api import app
    # pytest 同进程下,test_streaming.py 可能已把模块级流式限流桶设为 rate=1(并耗尽),
    # 而本测试要放开限流跑两条流式通路 —— 复位桶容量/速率/余量(限流语义由 test_streaming 覆盖)。
    _api._STREAM_LIMITER.capacity = 60
    _api._STREAM_LIMITER.rate = 1.0
    _api._STREAM_LIMITER.tokens = 60.0

    with TestClient(app) as c:
        # 1) 正常流式:SSE 收到 stage 进度 + act delta + 恰一个 result,无 error
        #    拓扑 5 → 3:领域 Agent 才走 DEFAULT_STAGES(含 act),主管走路由管线
        #    (recall→route→dispatch→aggregate),不会产出 act —— 故此处用 office。
        with c.stream("POST", "/api/task/stream",
                      json={"task": "帮我分析一下本月开销", "agent_id": "office"}) as resp:
            assert resp.status_code == 200, resp.status_code
            stages, deltas, results, errors = [], [], [], []
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                if "stage" in ev: stages.append(ev["stage"])
                if "delta" in ev: deltas.append(ev["delta"])
                if "result" in ev: results.append(ev["result"])
                if "error" in ev: errors.append(ev["error"])
            assert not errors, f"不应有 error 事件: {errors}"
            assert "act" in stages, f"应有 stage 进度事件(含 act): {stages}"
            assert len(deltas) > 0, "应有 act 逐 token delta 事件(证明流式透传经端点打通)"
            assert len(results) == 1, f"应恰有一个最终 result 事件,实得 {len(results)}"
            assert results[0].get("agent") == "office" and "result" in results[0], results[0]
        print(f"[1] /api/task/stream SSE(stage={len(stages)} delta={len(deltas)} result=1) OK")

        # 2) 【P3-3】主管不再回落领域 Agent:P1-1 pipeline 化后主管自身支持流式,
        #    stage 事件是主管管线七阶段,result.agent=supervisor,路由结论照常(stub 无关键词
        #    → 主管自身处理)。
        with c.stream("POST", "/api/task/stream",
                      json={"task": "随便问问", "agent_id": "supervisor"}) as resp:
            assert resp.status_code == 200, resp.status_code
            stages, results, errors = [], [], []
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                if "stage" in ev: stages.append(ev["stage"])
                if "result" in ev: results.append(ev["result"])
                if "error" in ev: errors.append(ev["error"])
            assert not errors, f"不应有 error 事件: {errors}"
            assert len(results) == 1, f"应恰有一个最终 result 事件,实得 {len(results)}"
            r0 = results[0]
            assert r0.get("agent") == "supervisor", \
                f"P3-3:主管走自身 pipeline,不再回落 assistant(实为 {r0.get('agent')})"
            assert "dispatch" in stages and "route" in stages, \
                f"应见主管管线 stage(route/dispatch): {stages}"
            assert r0["result"].get("dispatched_to") == "supervisor", \
                f"stub 无关键词 → 主管自身处理(实为 {r0['result'].get('dispatched_to')})"
        print("[2] agent_id=supervisor → 主管自身流式管线(P3-3,不再回落) OK")

    print("\nD1 /api/task/stream 验证通过")


if __name__ == "__main__":
    test_task_stream()
