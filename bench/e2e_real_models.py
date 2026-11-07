"""
真实多模型 E2E 冒烟（非 stub）· 数字产物

回答步骤 10「真实多模型 E2E」:逐个探测已配 Key 的 provider,各跑一条**真实**最小消息,
验证「非 stub、非降级、有真实输出」,产出量化报告。

opt-in:只对有真实 Key 的 provider 打真模型(要联网、会消耗额度);一个 Key 都没配则
优雅跳过(exit 0)——CI/离线环境绝不误判失败。

用法:
  python bench/e2e_real_models.py            # 只测当前环境已配 Key 的 provider
  FOXCLAW_E2E_SKIP_PROVIDER=google python ...  # 跳过某 provider(如无 SDK/额度)
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import foxclaw.core.web_search as ws  # noqa: E402
ws.needs_search = lambda q: False

from foxclaw.core.model_router import ModelRouter  # noqa: E402

_SYS = "你是 Foxclaw 助手。"
_MSG = [{"role": "user", "content": "请只回复两个字：你好"}]
_MAX_TOKENS = 64
# stub/故障 的特征文案(出现即视为"非真实成功")
_BAD_MARKERS = ("【未执行】", "【模型返回为空】", "【全部模型失败】", "【模型流式中断】", "STUB")


def _smoke(provider: str) -> dict:
    """对一个 provider 跑一条真实最小消息,返回观测结果。"""
    t0 = time.perf_counter()
    r = ModelRouter(provider=provider)
    info = r.info()
    if not info.get("client_ready"):
        return {"provider": provider, "ok": False, "lat_ms": 0,
                "output": "", "reason": "client 未就绪(SDK 缺失或无 Key): " +
                                         (info.get("fallback_reason") or "未知")}
    try:
        out = r.complete(_SYS, _MSG, max_tokens=_MAX_TOKENS, agent_id="bench")
        lat = (time.perf_counter() - t0) * 1000
    except Exception as e:
        return {"provider": provider, "ok": False, "lat_ms": (time.perf_counter() - t0) * 1000,
                "output": "", "reason": f"调用异常: {type(e).__name__}: {e}"}

    degraded = r.take_call_degraded()
    bad = any(m in out for m in _BAD_MARKERS)
    ok = bool(out) and not degraded and not bad
    reason = ""
    if degraded:
        reason = "本次调用降级(degraded)"
    elif bad:
        reason = "输出含降级/失败文案"
    return {"provider": provider, "ok": ok, "lat_ms": lat, "output": out, "reason": reason}


def main():
    skip = {s.strip().lower() for s in os.environ.get("FOXCLAW_E2E_SKIP_PROVIDER", "").split(",")
            if s.strip()}
    configured = [p["provider"] for p in ModelRouter.available_providers()
                  if p["configured"] and p["provider"] not in skip]

    print("=" * 64)
    print("Foxclaw · 真实多模型 E2E 冒烟(非 stub)")
    print("=" * 64)

    if not configured:
        print("未检测到任何已配置 Key 的 provider(见 .env.example)。")
        print("结论: 跳过(无真实模型可测)——离线/无额度环境属预期,非失败。")
        print("=" * 64)
        return 0

    print(f"检测到 {len(configured)} 个 provider: {', '.join(configured)}\n")
    n_ok = 0
    for p in configured:
        r = _smoke(p)
        mark = "✓" if r["ok"] else "✗"
        n_ok += 1 if r["ok"] else 0
        sample = (r["output"].strip().replace("\n", " ")[:36]) if r["output"] else "(空)"
        print(f"[{mark}] {p:12s}  {r['lat_ms']:7.0f} ms  → {sample}")
        if r["reason"]:
            print(f"     原因: {r['reason']}")

    print("\n" + "=" * 64)
    print(f"结论: {n_ok}/{len(configured)} 个真实模型冒烟通过 — "
          f"{'✓ 多模型真实链路可用' if n_ok == len(configured) else '✗ 有 provider 未通过(见上)'}")
    print("=" * 64)
    return 0 if n_ok == len(configured) else 1


if __name__ == "__main__":
    sys.exit(main())
