"""
能力注册中枢(P1-3:God Object → Composition Root)测试 —— 全离线。

覆盖:
  1. CapabilityRegistry:register / get / register_lazy(首次才构造且只构造一次) / has / names
  2. 打断两处直接环(结构):
     · Agent 不再持有 _search_runtime,改持 _caps(经中枢解析 search_runtime)
     · Orchestrator 不再把 agents 存为实例字段,改为经中枢解析的 property
  3. 行为保持:cross_request 经中枢解析 agents 仍工作;search_internal 经中枢解析 search_runtime
"""
import os, sys, asyncio, tempfile, shutil
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_EMBEDDING_MODEL"] = "foxclaw-nonexistent-fast"
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.kernel import CapabilityRegistry


def test_registry_primitive():
    print("\n【1】CapabilityRegistry:register / lazy / has / names")
    reg = CapabilityRegistry()
    reg.register("a", 123)
    assert reg.get("a") == 123
    assert reg.get("missing") is None and reg.get("missing", "d") == "d"

    calls = {"n": 0}
    def factory():
        calls["n"] += 1
        return {"built": True}
    reg.register_lazy("b", factory)
    assert reg.has("b") and "b" not in reg._caps, "惰性:注册后未构造"
    v1 = reg.get("b"); v2 = reg.get("b")
    assert v1 is v2 and calls["n"] == 1, "惰性只构造一次并缓存"
    assert set(reg.names()) >= {"a", "b"}
    print(f"  ✓ register/get/lazy(构造 {calls['n']} 次)/has/names 均正确")


def test_cycles_broken_structural():
    print("\n【2】结构:两处直接环被打断")
    d = tempfile.mkdtemp(prefix="cap_os_")
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        # 环1:Agent 不再直接持 search_runtime
        assert not hasattr(os_.finance, "_search_runtime"), "Agent 不应有 _search_runtime 直接引用"
        assert os_.finance._caps is os_.capabilities, "Agent 改持能力中枢"
        # 环2:Orchestrator 不再把 agents 存为实例字段(改为经中枢解析的 property)
        assert "agents" not in os_.orchestrator.__dict__, "orchestrator 不应把 agents 存为实例字段"
        assert os_.orchestrator._caps is os_.capabilities
        assert os_.orchestrator.agents is os_._agents, "orchestrator.agents 经中枢解析"
        print("  ✓ Agent→search / Orchestrator→agents 的直接互持已改为中枢解析")
        print(f"    capabilities.names() = {os_.capabilities.names()}")
        asyncio.run(os_.aclose())
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_behavior_preserved():
    print("\n【3】行为保持:编排 + 双向搜索 经中枢解析仍工作")
    d = tempfile.mkdtemp(prefix="cap_beh_")
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        # 跨 Agent 编排(orchestrator 经中枢解析 finance)
        r = os_.supervisor.cross_request(
            from_agent="admin", to_agent="finance", request_type="budget_request",
            payload={"amount": 150, "category": "travel", "entity": "高铁", "account": "default"},
            reason="出差")
        assert r["status"] in ("approved", "confirm_required", "rejected"), f"裁决异常 {r}"
        print(f"  ✓ cross_request 经中枢解析 agents → 状态 {r['status']}")
        # Agent 反向调用内部搜索(经中枢解析 search_runtime)
        res = os_.finance.search_internal("测试查询")
        assert res is not None, "search_internal 应经中枢解析到 search_runtime"
        # 中枢缓存:两次解析同一实例
        assert os_.capabilities.get("search_runtime") is os_.search_runtime
        print("  ✓ search_internal 经中枢解析 search_runtime(缓存一致)")
        asyncio.run(os_.aclose())
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_lazy_build_once_concurrent():
    print("\n【4】F1:并发下 per-name 惰性只构造一次(慢构造不持全局锁)")
    import threading, time
    reg = CapabilityRegistry()
    calls = {"n": 0}
    lock = threading.Lock()
    def slow():
        time.sleep(0.05)
        with lock:
            calls["n"] += 1
        return object()
    reg.register_lazy("x", slow)
    results = []
    def worker():
        results.append(reg.get("x"))
    ts = [threading.Thread(target=worker) for _ in range(12)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert calls["n"] == 1, f"并发下惰性应只构造 1 次,实得 {calls['n']}"
    assert len({id(r) for r in results}) == 1, "12 线程应拿到同一实例"
    # 不同能力不互相阻塞(构造锁 per-name;此处只验证正确性:各自构造一次)
    reg.register_lazy("y", lambda: object())
    assert reg.get("y") is reg.get("y")
    print("  ✓ 12 线程并发 get 同一惰性 → 只构造 1 次、同一实例;不同能力独立")


def test_hkc_single_init_concurrent():
    print("\n【5】F2:hkc 惰性属性并发首访只装配一次(防句柄泄漏)")
    import threading
    d = tempfile.mkdtemp(prefix="cap_hkc_")
    try:
        from foxclaw import Foxclaw
        import foxclaw.knowledge as K
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        orig = K.init_hkc
        cnt = {"n": 0}
        clock = threading.Lock()
        def counting(*a, **k):
            with clock:
                cnt["n"] += 1
            return orig(*a, **k)
        K.init_hkc = counting
        try:
            out = []
            def w():
                out.append(os_.hkc)
            ts = [threading.Thread(target=w) for _ in range(10)]
            for t in ts: t.start()
            for t in ts: t.join()
            assert cnt["n"] == 1, f"并发首访 hkc 应只装配 1 次,实得 {cnt['n']}"
            assert len({id(o) for o in out}) == 1, "10 线程应拿到同一 HKCService"
            print("  ✓ 10 线程并发首访 os_.hkc → init_hkc 只调 1 次")
        finally:
            K.init_hkc = orig
        asyncio.run(os_.aclose())
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    print("=" * 60)
    print("能力注册中枢 · Composition Root(P1-3)")
    print("=" * 60)
    test_registry_primitive()
    test_cycles_broken_structural()
    test_behavior_preserved()
    test_lazy_build_once_concurrent()
    test_hkc_single_init_concurrent()
    print("\n" + "=" * 60)
    print("✓ P1-3 验证完成:能力中枢 + 两处直接环打断 + 行为保持")
    print("=" * 60)


if __name__ == "__main__":
    main()
