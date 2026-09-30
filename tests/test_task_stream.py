"""D1:/api/task/stream SSE —— 任务执行流式化(逐 stage 进度 + act 逐 token + 最终 result)。
与 test_streaming.py 的 /api/chat/stream 端点测试对称:那条测对话直答流式,这条测**走完整认知
管线**的任务流式。核心逻辑(run_task_stream)在 test_agent_lifecycle 已直测,这里补端点级 SSE 打通。"""
import os, sys, tempfile, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["HESTIA_SHADOW_ROUNDS"] = "6"
os.environ.setdefault("HESTIA_EMBEDDING_MODEL", "hestia-nonexistent-fast")
os.environ["HESTIA_STREAM_RATE"] = "60"   # 放开限流(限流由 test_streaming 覆盖),这里只验通路
os.environ["HESTIA_DATA_DIR"] = tempfile.mkdtemp(prefix="hestia_taskstream_")
# 强制 stub(离线/确定性/免费):关掉 .env,使进程环境变量成为唯一来源。
# (旧写法是逐个把 key 置空以依赖 load_dotenv(override=False)——但那挡不住「先 pop 再
#  import」的路径,也挡不住 .env 里的 HESTIA_MCP_SERVER 等非 key 变量。见 api.py 说明。)
os.environ["HESTIA_SKIP_DOTENV"] = "1"
for _k in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
           "GOOGLE_API_KEY", "DASHSCOPE_API_KEY", "HESTIA_API_KEY"):
    os.environ[_k] = ""

print("=" * 60); print("D1 · /api/task/stream 任务流式化验证"); print("=" * 60)

from fastapi.testclient import TestClient
from hestia.api import app

with TestClient(app) as c:
    # 1) 正常流式:SSE 收到 stage 进度 + act delta + 恰一个 result,无 error
    with c.stream("POST", "/api/task/stream",
                  json={"task": "帮我分析一下本月开销", "agent_id": "assistant"}) as resp:
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
        assert results[0].get("agent") == "assistant" and "result" in results[0], results[0]
    print(f"[1] /api/task/stream SSE(stage={len(stages)} delta={len(deltas)} result=1) OK")

    # 2) 主管路由不走流式管线 → 端点回落 assistant(仍 200 + 正常出 result,不崩)
    with c.stream("POST", "/api/task/stream",
                  json={"task": "随便问问", "agent_id": "supervisor"}) as resp:
        assert resp.status_code == 200, resp.status_code
        got_result = False
        for line in resp.iter_lines():
            if line.startswith("data: ") and "result" in json.loads(line[6:]):
                got_result = True
        assert got_result, "supervisor 请求应回落 assistant 并正常产出 result"
    print("[2] agent_id=supervisor → 回落 assistant,正常产出(不跑主管路由) OK")

print("\nD1 /api/task/stream 验证通过")
