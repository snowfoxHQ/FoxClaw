"""
混沌故障注入（Chaos Engineering）· 数字产物

补足 bench 三件套的「故障维度」:
  · concurrency.py —— 共享库并发写(无故障)
  · load_test.py    —— HTTP 高并发负载(无故障)
  · chaos.py(本文件) —— 故障注入:LLM 空响应/异常/fallback/断路器,验证降级语义 + 恢复

验证 ModelRouter(所有 Agent 的 LLM 唯一咽喉)在故障下的三件事:
  1. 降级语义正确:空响应/全失败**不伪装成功**(对齐 base.py §二「真相优先」);
  2. fallback 链:主 provider 故障 → 自动回退到已配的次 provider;
  3. 断路器:故障 provider 进冷却、成功解冻(惊群防护),系统可恢复。

离线:不联网、不碰真实 Key,纯 monkeypatch 注入故障。
用法:HF_HUB_OFFLINE=1 python bench/chaos.py
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# 关闭联网触发(chaos 只测 LLM 咽喉,不触发搜索)
import foxclaw.core.web_search as ws  # noqa: E402
ws.needs_search = lambda q: False

from foxclaw.core.model_router import ModelRouter, PROVIDER_ENV  # noqa: E402

_SYS = "你是 Foxclaw 助手。"
_MSG = [{"role": "user", "content": "用一句话总结量子计算"}]


def _make_router(primary="deepseek", fallback=None):
    """构造一个走真实路径(非 stub)的 router,并把 _client 设成非 None,
    以便 monkeypatch _call_provider 注入故障。fallback 指定次 provider 并伪造其 key。"""
    if fallback:
        os.environ[PROVIDER_ENV[fallback]] = "chaos-test-key"
    r = ModelRouter(provider=primary)
    r._client = object()                     # 使 is_stub == False
    r._clients[primary] = object()
    if fallback:
        r._clients[fallback] = object()
    return r


def _boom(provider, client, model, system, messages, max_tokens, images=None):
    raise RuntimeError("chaos: upstream timeout")


def case1_empty_response():
    """[1] 空响应故障:provider 返回空串 → degraded + 显式失败文案,不伪装成功。"""
    r = _make_router("deepseek")
    r._call_provider = lambda *a, **k: ("", None)
    out = r.complete(_SYS, _MSG)
    ok = ("【模型返回为空】" in out) and r.is_degraded()
    return ok, ("degraded=" + str(r.is_degraded())), out[:40]


def case2_call_failure():
    """[2] 调用异常故障:LLM 抛异常 → 全部失败文案 + degraded,绝不拼接 stub 成功文案。"""
    r = _make_router("deepseek")
    r._call_provider = _boom
    out = r.complete(_SYS, _MSG)
    ok = ("【全部模型失败】" in out) and r.is_degraded() and ("已完成" not in out)
    return ok, ("degraded=" + str(r.is_degraded()) + " 伪装成功=" + str("已完成" in out)), out[:40]


def case3_fallback_chain():
    """[3] fallback 链:主 provider 抛异常 → 自动回退到已配次 provider 并记录原因。"""
    r = _make_router("deepseek", fallback="openai")
    calls = []

    def _flaky(provider, client, model, system, messages, max_tokens, images=None):
        calls.append(provider)
        if provider == "deepseek":
            raise RuntimeError("chaos: deepseek down")
        return "openai 兜底回复", None

    r._call_provider = _flaky
    out = r.complete(_SYS, _MSG)
    ok = (out == "openai 兜底回复") and (calls[-1] == "openai") and bool(r.fallback_reason)
    return ok, ("调用顺序=" + str(calls) + " 回退原因=" + r.fallback_reason), out[:40]


def case4_circuit_breaker():
    """[4] 断路器:provider 失败后进入冷却,立即重试被跳过(不再紧密重试)。"""
    r = _make_router("deepseek")
    r._call_provider = _boom
    r.complete(_SYS, _MSG)                    # 第一次失败 → 进入 cooldown
    cooled = "deepseek" in r._cooldown
    t0 = time.perf_counter()
    r.complete(_SYS, _MSG)                    # 第二次:cooldown 未过期 → 直接跳过,不等超时
    fast = (time.perf_counter() - t0) < 0.5   # 跳过热 provider,应远快于真实超时
    ok = cooled and fast and r.is_degraded()
    return ok, (f"冷却中={cooled} 跳过耗时={((time.perf_counter()-t0)*1000):.0f}ms"), "断路器生效"


def case5_recovery():
    """[5] 恢复:故障清除(cooldown 过期/成功)后,同一 provider 恢复正常服务。"""
    r = _make_router("deepseek")
    r._call_provider = _boom
    r.complete(_SYS, _MSG)
    assert "deepseek" in r._cooldown           # 先确认在冷却
    r._cooldown["deepseek"] = 0                # 模拟冷却过期
    r._call_provider = lambda *a, **k: ("恢复后的正常回复", None)
    out = r.complete(_SYS, _MSG)
    # 注:degraded 是「曾降级」的累积运维标志(不因恢复清零),恢复语义看「解冻 + 成功返回」。
    ok = (out == "恢复后的正常回复") and ("deepseek" not in r._cooldown)
    return ok, ("解冻=" + str("deepseek" not in r._cooldown)), out[:40]


def main():
    cases = [
        ("空响应 → degraded(不伪装成功)", case1_empty_response),
        ("调用异常 → 全部失败(不拼接 stub)", case2_call_failure),
        ("fallback 链(主失败→次兜底)", case3_fallback_chain),
        ("断路器(失败进冷却·重试跳过)", case4_circuit_breaker),
        ("恢复(冷却过期→正常服务)", case5_recovery),
    ]
    print("=" * 64)
    print("Foxclaw · 混沌故障注入(ModelRouter 咽喉)")
    print("=" * 64)
    n_ok = 0
    for name, fn in cases:
        try:
            ok, detail, sample = fn()
            n_ok += 1 if ok else 0
            print(f"\n[{name}]")
            print(f"  {'✓ 通过' if ok else '✗ 失败'}  {detail}")
            print(f"  输出样例: {sample}…")
        except Exception as e:
            print(f"\n[{name}]\n  ✗ 异常: {type(e).__name__}: {e}")
    print("\n" + "=" * 64)
    print(f"结论: {n_ok}/{len(cases)} 混沌用例通过 — "
          f"{'✓ 故障下降级语义正确、fallback/断路器/恢复均生效' if n_ok == len(cases) else '✗ 存在故障处理缺口'}")
    print("=" * 64)
    return 0 if n_ok == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
