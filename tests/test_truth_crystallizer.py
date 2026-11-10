"""
真值走 HKC/KEE(v2 ④ Step1)测试 —— 全离线,假 HKC(不调 LLM)。

覆盖:
  1. 默认关:on_truth 不结晶(行为不变)
  2. 启用:后台把真值 fact 经 hkc.client.ingest_text(KDE→KEE)结晶
  3. 被拦截任务跳过 / HKC 不可用跳过(不崩)
  4. 真实接线:Foxclaw 把结晶器订阅到 truth.appended;发该事件即触发(启用+假HKC)
"""
import os, sys, asyncio, tempfile, shutil, types
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_EMBEDDING_MODEL"] = "foxclaw-nonexistent-fast"
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.knowledge import TruthCrystallizer


class _Client:
    def __init__(self): self.ingested = []
    def ingest_text(self, text, source_title="", domain="", **kw):
        self.ingested.append((text, domain)); return None


class _HKC:
    def __init__(self, available=True): self.available = available; self.client = _Client()


def _msg(payload): return types.SimpleNamespace(payload=payload)


def test_disabled_default():
    print("\n【1】默认关:不结晶(行为不变)")
    hkc = _HKC()
    tc = TruthCrystallizer(hkc_provider=lambda: hkc, enabled=False)
    tc.on_truth(_msg({"truth": {"fact": "毕昇发明活字印刷术"}, "agent_id": "office"}))
    tc.drain()
    assert hkc.client.ingested == [], "默认关不应结晶"
    tc.close()
    print("  ✓ enabled=False → 零结晶")


def test_enabled_crystallizes():
    print("\n【2】启用:后台经 ingest_text(KDE→KEE)结晶真值")
    hkc = _HKC()
    tc = TruthCrystallizer(hkc_provider=lambda: hkc, enabled=True)
    tc.on_truth(_msg({"truth": {"fact": "毕昇发明活字印刷术"}, "agent_id": "office"}))
    tc.drain()
    assert hkc.client.ingested == [("毕昇发明活字印刷术", "office")], hkc.client.ingested
    tc.close()
    print(f"  ✓ 结晶: {hkc.client.ingested}(domain 取 agent_id)")


def test_blocked_and_unavailable_skipped():
    print("\n【3】被拦截 / HKC 不可用 → 跳过(不崩)")
    hkc = _HKC()
    tc = TruthCrystallizer(hkc_provider=lambda: hkc, enabled=True)
    tc.on_truth(_msg({"truth": {"fact": "x"}, "agent_id": "finance", "blocked": True}))
    tc.drain()
    assert hkc.client.ingested == [], "被拦截任务不结晶"
    un = _HKC(available=False)
    tc2 = TruthCrystallizer(hkc_provider=lambda: un, enabled=True)
    tc2.on_truth(_msg({"truth": {"fact": "y"}, "agent_id": "finance"}))
    tc2.drain()
    assert un.client.ingested == [], "HKC 不可用则跳过"
    tc.close(); tc2.close()
    print("  ✓ blocked 跳过;HKC 不可用跳过、不崩")


def test_pending_bounded_no_leak():
    print("\n【3b】深审回归:持续结晶下 _pending 不无界增长(prune 已完成 Future)")
    hkc = _HKC()
    tc = TruthCrystallizer(hkc_provider=lambda: hkc, enabled=True)
    for i in range(200):
        tc.on_truth(_msg({"truth": {"fact": f"事实{i}"}, "agent_id": "office"}))
    tc.drain()
    tc.on_truth(_msg({"truth": {"fact": "末尾"}, "agent_id": "office"}))
    import time as _t
    for _ in range(50):
        if all(f.done() for f in tc._pending):
            break
        _t.sleep(0.02)
    assert len(tc._pending) <= 2, f"_pending 应只留在飞行中的,实得 {len(tc._pending)}"
    assert len(hkc.client.ingested) == 201
    tc.close()
    print(f"  ✓ 提交 201 次,_pending 稳定在 {len(tc._pending)}(非 201)→ 无泄漏")


def test_integration_wiring():
    print("\n【4】真实接线:Foxclaw 订阅 truth.appended → 触发结晶")
    d = tempfile.mkdtemp(prefix="truthx_")
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        fake = _HKC()
        os_.truth_crystallizer._hkc_provider = lambda: fake
        os_.truth_crystallizer.enabled = True
        os_.system_bus.publish("truth.appended",
                               {"truth": {"fact": "测试真值事实"}, "agent_id": "finance"})
        os_.truth_crystallizer.drain()
        assert fake.client.ingested and fake.client.ingested[0][0] == "测试真值事实", \
            f"应经订阅触发结晶,实得 {fake.client.ingested}"
        print(f"  ✓ 发 truth.appended → 结晶 {fake.client.ingested}")
        asyncio.run(os_.aclose())
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    print("=" * 60)
    print("真值走 HKC/KEE · TruthCrystallizer(v2 ④ Step1)")
    print("=" * 60)
    test_disabled_default()
    test_enabled_crystallizes()
    test_blocked_and_unavailable_skipped()
    test_pending_bounded_no_leak()
    test_integration_wiring()
    print("\n" + "=" * 60)
    print("✓ Step1 验证完成:默认关(双写不变) + 启用后台结晶 + 跳过降级 + 订阅接线")
    print("=" * 60)


if __name__ == "__main__":
    main()
