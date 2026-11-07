"""
真值流全路径触发(P2)测试 —— 全离线(stub LLM,bogus embedding 让 harness 走 hash)。

审计 P2 前提"删重复落库"已被代码推翻(TruthPipeline 写的是**有意的共享真值记录**,
非私有情景记忆的重复)。真正的问题是"共享真值记录只在主管分发路径产生"的不一致。
本次修复:统一模板 run_task 也发 execution.completed → 直连领域 Agent 的任务同样进真值链;
主管分发时子 Agent 带 _no_emit,由主管统一发(带 schedule),避免双发。

覆盖:
  1. _no_emit 守卫:context['_no_emit']=True 时子 Agent 不自发 execution.completed
  2. 全路径触发:直连领域 Agent 任务发**恰好一条** execution.completed
  3. 共享真值记录:直连任务经 TruthPipeline 产生 shared=True 记录,别的 Agent 能召回
"""
import os, sys, asyncio, tempfile, shutil
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_EMBEDDING_MODEL"] = "foxclaw-nonexistent-fast"   # harness 走 hash,快
sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    print("=" * 60)
    print("真值流 · 全路径触发(P2)")
    print("=" * 60)
    d = tempfile.mkdtemp(prefix="truth_")
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        seen = []
        os_.event_spine.project(lambda e: seen.append(e.topic))

        # 1. _no_emit 守卫:子 Agent 不自发
        print("\n【1】_no_emit 守卫:分发时子 Agent 不自发 execution.completed")
        asyncio.run(os_.finance.run_task("守卫测试", {"_no_emit": True}))
        assert "execution.completed" not in seen, "带 _no_emit 时不应发 execution.completed"
        print("  ✓ context['_no_emit']=True → 子 Agent 未自发(留给主管统一发)")

        # 2. 全路径触发:直连任务发恰好一条
        print("\n【2】全路径触发:直连领域 Agent 任务发恰好一条 execution.completed")
        before = seen.count("execution.completed")
        asyncio.run(os_.finance.run_task("分析我的月度开支"))
        emitted = seen.count("execution.completed") - before
        assert emitted == 1, f"直连任务应发恰好 1 条,实得 {emitted}"
        print(f"  ✓ 直连 finance.run_task → execution.completed ×{emitted}")

        # 3. 共享真值记录:别的 Agent 能召回
        print("\n【3】共享真值记录:直连任务经 TruthPipeline 产生 shared 记录,跨 Agent 可见")
        shared = os_.hmr.recall(query="分析我的月度开支 处理", agent_id="office", top_k=10)
        shared_hits = [m for m in shared if m.get("shared")]
        assert shared_hits, "直连任务应产生 shared=True 真值记录(修复:此前仅分发路径有)"
        print(f"  ✓ office 召回到 {len(shared_hits)} 条共享真值记录(来自 finance 的直连任务)")
        print(f"    样例: {shared_hits[0].get('content','')[:50]!r}")

        # 4. 被拦截的直连任务:发 blocked=True、不落共享真值记录、CognitiveState 不崩
        print("\n【4】被拦截的直连任务:blocked=True + 不落共享记录 + 观测不崩")
        import types
        from foxclaw.agents.base import HarnessBlockedError
        async def boom(self, task, ctx, sid, mem):
            raise HarnessBlockedError(types.SimpleNamespace(reason="越界"), None)
        os_.finance._execute = types.MethodType(boom, os_.finance)
        blocked_flags = []
        os_.event_spine.project(
            lambda e: blocked_flags.append(e.payload.get("blocked"))
            if e.topic == "execution.completed" else None)
        b_before = len([x for x in blocked_flags if x is True])
        r = asyncio.run(os_.finance.run_task("危险越界任务"))
        b_emitted = [x for x in blocked_flags if x is True]
        assert r.get("error") == "harness_blocked"
        assert len(b_emitted) - b_before == 1, "被拦截直连应发恰好一条 blocked=True"
        # 精确判定:blocked 任务本身没产生真值记录(按内容过滤;语义召回可能带回 check2 的旧记录)
        own = [m for m in os_.hmr.recall(query="危险越界任务 处理", agent_id="office", top_k=10)
               if m.get("shared") and "危险越界" in m.get("content", "")]
        assert len(own) == 0, "被拦截任务不应产生自己的共享真值记录(TruthPipeline 跳过 blocked)"
        snap = os_.cognitive_state.snapshot()   # 缺 schedule 的直连事件不应让观测崩溃
        assert "active_agent" in snap
        print(f"  ✓ blocked=True ×1、无共享记录、CognitiveState 快照正常(focus={snap.get('semantic_focus')})")

        asyncio.run(os_.aclose())
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("\n" + "=" * 60)
    print("✓ P2 验证完成:全路径触发 + _no_emit 防双发 + 共享真值记录一致")
    print("=" * 60)


def test_truth_flow():
    main()


if __name__ == "__main__":
    main()
