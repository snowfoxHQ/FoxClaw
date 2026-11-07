"""MCP ask_foxclaw "不污染记忆":外部驱动的任务不写 HMR(防外部灌垃圾污染个人记忆),
正常任务照写。验证抑制标志随 to_thread→scheduler.run→asyncio.run 上下文传到 _store_memory。"""
import os, sys, tempfile, asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_mcp_no_memory():
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"
    os.environ["FOXCLAW_SKIP_DOTENV"] = "1"      # 隔离开发机 .env,保证测试环境自洽
    os.environ.setdefault("FOXCLAW_EMBEDDING_MODEL", "foxclaw-nonexistent-fast")
    os.environ["HF_HUB_OFFLINE"] = "1"         # 无网环境:跳过 huggingface 联网重试,直接用本地缓存
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_nomem_")

    from foxclaw import Foxclaw

    o = Foxclaw(data_dir=os.environ["FOXCLAW_DATA_DIR"])
    calls = []
    o.hmr.ingest = lambda *a, **k: calls.append(1)   # spy:数 HMR 写入次数


    async def dispatch_suppressed():
        """完全复刻 ask_foxclaw 的派发路径:to_thread → set 抑制 → scheduler.run → asyncio.run。"""
        def _work():
            from foxclaw.agents.base import _suppress_memory
            _suppress_memory.set(True)
            async def _t():
                o.supervisor._store_memory("外部驱动任务的内容(应被抑制)")
            return o.scheduler.run(lambda: asyncio.run(_t()), agent_id="assistant", task="t")
        return await asyncio.to_thread(_work)


    asyncio.run(dispatch_suppressed())
    suppressed = len(calls)
    o.supervisor._store_memory("正常任务内容(应写入)")
    normal = len(calls) - suppressed

    assert suppressed == 0, f"污染!MCP 外部任务写了 {suppressed} 条记忆"
    assert normal > 0, "正常任务应正常写记忆"
    print(f"MCP ask_foxclaw 不污染记忆 OK(抑制路径写入={suppressed}, 正常路径写入={normal})")

if __name__ == "__main__":
    test_mcp_no_memory()
