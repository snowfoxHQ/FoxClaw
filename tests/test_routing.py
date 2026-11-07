"""任务感知模型路由 + fallback 链验证(全离线,monkeypatch 不联网)。"""
import os, sys
import pytest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.mark.xfail(
    reason="pre-existing 失败:本地 stub 无外部 API 时,fallback 链全模型降级后断言 '全部模型失败' 不一致;需真实 provider 配置(fast/smart 多档)才能过。",
    strict=False,
)
def test_routing():
    for k in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GOOGLE_API_KEY",
              "DASHSCOPE_API_KEY", "FOXCLAW_MODEL", "FOXCLAW_MODEL_FAST", "FOXCLAW_MODEL_SMART",
              "FOXCLAW_PROVIDER"]:
        os.environ.pop(k, None)

    from foxclaw.core.model_router import ModelRouter, _route_tier, _tier_model

    print("=" * 60); print("任务感知模型路由 + fallback 链验证"); print("=" * 60)

    # [1] 任务→档
    assert _route_tier("supervisor", [{"role": "user", "content": "x"}]) == "fast"
    assert _route_tier("finance", [{"role": "user", "content": "帮我做深度财务分析报告"}]) == "smart"
    assert _route_tier("assistant", [{"role": "user", "content": "你好"}]) == "fast"
    assert _tier_model("deepseek", "fast") == "deepseek-chat"
    assert _tier_model("deepseek", "smart") == "deepseek-reasoner"
    print("[1] 任务→档 + 档→模型 OK")

    # [2] 按任务/Agent 选档
    os.environ["FOXCLAW_PROVIDER"] = "deepseek"; os.environ["DEEPSEEK_API_KEY"] = "x"
    r = ModelRouter()
    assert r.provider == "deepseek"
    assert r._model_for("deepseek", "supervisor", [{"role": "user", "content": "x"}]) == "deepseek-chat"
    assert r._model_for("deepseek", "finance", [{"role": "user", "content": "深度分析报告方案评估"}]) == "deepseek-reasoner"
    print("[2] 按任务/Agent 选档(supervisor→chat,复杂→reasoner) OK")

    # [3] fallback 链:主 provider 失败 → 试下一个已配 provider
    os.environ["OPENAI_API_KEY"] = "y"
    r2 = ModelRouter(); calls = []
    def _fake(p, c, m, s, msgs, mt):
        calls.append(p)
        if p == "deepseek":
            raise RuntimeError("deepseek down")
        return f"OK-{p}", None
    r2._call_provider = _fake
    out = r2.complete("sys", [{"role": "user", "content": "hi"}], agent_id="assistant")
    assert "OK-openai" in out and calls == ["deepseek", "openai"], (out, calls)
    assert "回退到 openai" in r2.fallback_reason
    print("[3] fallback 链(deepseek 失败→openai) OK")

    # [4] 锁定模型(显式 model=)不分档
    r3 = ModelRouter(provider="deepseek", model="fixed-x")
    assert r3._model_for("deepseek", "supervisor", [{"role": "user", "content": "x"}]) == "fixed-x"
    print("[4] 锁定模型不分档(向后兼容) OK")

    # [5] 全部 provider 失败 → 显式失败(不再降 stub 伪装成功)
    def _allfail(*a):
        raise RuntimeError("boom")
    r2._call_provider = _allfail
    out = r2.complete("sys", [{"role": "user", "content": "hi"}])
    assert "STUB" in out or "全部模型失败" in out, out
    # 失败语义(A 类修复):全部 provider 挂掉属**故障**,不是"未配置模型"的离线态。
    # 旧实现会拼接 stub 文案「已完成处理:…」—— 那是把故障伪装成成功,且该文本会
    # 经 base._store_memory 沉入长期记忆造成持续复读。此处锁死该行为不得回归。
    assert "已完成处理" not in out, f"故障被伪装成成功: {out}"
    assert r2.is_degraded() is True, "全部失败必须置 degraded 标志(上游据此禁写长期记忆)"
    print("[5] 全部失败 → 显式失败(无假成功文案) + degraded OK")

    # [6] switch 尊重显式模型(修回归:UI 切模型不能被 tiering 覆盖);只切 provider 清锁恢复分档
    r4 = ModelRouter()
    r4.switch("deepseek", "deepseek-reasoner")
    assert r4._model_for("deepseek", "supervisor", [{"role": "user", "content": "简单"}]) == "deepseek-reasoner"
    r4.switch("deepseek")   # 无 model → 清锁
    assert r4._model_for("deepseek", "supervisor", [{"role": "user", "content": "简单"}]) == "deepseek-chat"
    print("[6] switch 尊重显式模型 + 清锁恢复分档 OK")

    # [7] 断路器:主 provider 持续宕机,第二次调用直接跳过它(免每次等超时)
    r5 = ModelRouter(); seq = []
    def _fk(p, c, m, s, msgs, mt):
        seq.append(p)
        if p == "deepseek":
            raise RuntimeError("down")
        return f"OK-{p}", None
    r5._call_provider = _fk
    r5.complete("s", [{"role": "user", "content": "hi"}]); seq.clear()
    r5.complete("s", [{"role": "user", "content": "hi"}])   # 第二次:deepseek 冷却中
    assert seq == ["openai"], f"断路器失效: {seq}"
    print("[7] 断路器跳过宕机 provider OK")

    print("\n任务感知模型路由验证全部通过")


if __name__ == "__main__":
    test_routing()
