"""
知识核心(HKC 融入)测试 —— 全离线,embedding=stub,不需 LLM。

覆盖:
  1. init_hkc 同进程装配 → available;空库混合检索返回 [];stats 有 data_dir
  2. H1 资源修复:close() 后能 rmtree 数据目录(无 Windows WinError 32)——
     即 event_bus/id_gen/graph_store/synthesis 四个 SQLite 句柄全部释放
  3. close() 幂等
  4. 经 Foxclaw 惰性装配:未访问 .hkc 前不开句柄;访问后装配;aclose() 释放后可删目录

摄入(ingest)需真实 LLM,不在离线回归内跑;这里只验证融合层的装配/降级/资源契约。
"""
import sys, tempfile, shutil, asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.knowledge import init_hkc


def test_assemble_search_close():
    print("\n【1】init_hkc 装配 + 空库检索 + stats + close 后可删目录(H1)")
    d = tempfile.mkdtemp(prefix="hkc_test_")
    ok_rmtree = False
    try:
        svc = init_hkc(d, api_key=None)
        assert svc is not None and svc.available, "依赖已装,HKC 应装配成功"
        print("  ✓ 同进程装配成功 available=True")

        hits = svc.client.search("任意不存在的查询", mode="hybrid", top_k=5)
        assert isinstance(hits, list) and len(hits) == 0
        print(f"  ✓ 空库混合检索返回空: {hits}")

        s = svc.stats()
        assert "data_dir" in s and "total_kus" in s
        print(f"  ✓ stats: total_kus={s.get('total_kus')} embedding={s.get('embedding')!r}")

        svc.close()
        svc.close()
        print("  ✓ close() 幂等")
    finally:
        shutil.rmtree(d)
        ok_rmtree = True
    assert ok_rmtree
    print("  ✓ close 后 rmtree 成功 → H1 资源泄漏修复生效(无 WinError 32)")


def test_via_foxclawos_lazy_teardown():
    print("\n【2】经 Foxclaw 惰性装配 + aclose teardown 全链路")
    d = tempfile.mkdtemp(prefix="hkc_os_")
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        assert os_._hkc_service is None and os_._hkc_inited is False
        print("  ✓ 构造 OS 后未装配 HKC(惰性,未开 db 句柄)")

        hkc = os_.hkc
        assert os_._hkc_inited is True
        assert hkc is not None and hkc.available
        print("  ✓ 首次访问 os_.hkc 触发装配 available=True")

        hits = hkc.client.search("空库查询", top_k=3)
        assert hits == []
        print("  ✓ 经 DirectClient 检索空库返回 []")

        asyncio.run(os_.aclose())
        print("  ✓ aclose() 释放浏览器 + HKC 资源")
    finally:
        shutil.rmtree(d)
    print("  ✓ teardown 后 rmtree 成功(HKC 句柄经 aclose 释放)")


def test_ability_readonly_surface():
    print("\n【3】第2档 Step1:ACE 能力只读面(list/compile 证据 gate/neighbors)——无 LLM")
    d = tempfile.mkdtemp(prefix="hkc_abil_")
    svc = None
    try:
        svc = init_hkc(d, api_key=None)
        assert svc is not None and svc.available
        from hkc_sdk.exceptions import HKCInsufficientCoverage, HKCNotFoundError
        keys = svc.client.list_abilities()
        assert isinstance(keys, list) and len(keys) > 0, f"应有内置能力定义,实得 {keys}"
        print(f"  ✓ list_abilities 返回 {len(keys)} 个能力: {keys[:4]}...")
        gated = False
        try:
            svc.client.compile_ability(keys[0])
        except HKCInsufficientCoverage as e:
            gated = True
            assert hasattr(e, "missing_skills") and hasattr(e, "coverage")
        assert gated, "空知识库编译能力应被证据 gate 拦(覆盖不足)"
        print("  ✓ 空库 compile_ability 被证据 gate 拦(覆盖不足→能力不予授予)")
        raised = False
        try:
            svc.client.neighbors("KU_nonexistent", max_depth=2, top_k=10)
        except HKCNotFoundError:
            raised = True
        assert raised, "不存在 KU 应抛 HKCNotFoundError(SDK 契约)"
        print("  ✓ neighbors(不存在 KU) 抛 HKCNotFoundError(SDK 契约;API 端点已删,见 V3 清理)")
    finally:
        if svc is not None:
            svc.close()
        shutil.rmtree(d, ignore_errors=True)
    print("  ✓ 只读能力面成立:枚举 + 证据 gate + 图邻域(全无 LLM/零成本)")


def test_agent_available_abilities():
    print("\n【4】第2档 Step2:Agent.available_abilities() 证据驱动自省(只读,不改执行)")
    d = tempfile.mkdtemp(prefix="hkc_selfintro_")
    os_ = None
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        abils = os_.finance.available_abilities()
        assert isinstance(abils, list) and len(abils) > 0, f"应枚举出能力,实得 {abils}"
        assert all(a["sufficient"] is False for a in abils), \
            f"空库→全部证据 gate 拦(sufficient=False),实得 {[a['sufficient'] for a in abils]}"
        assert all("missing_skills" in a for a in abils), "未达标应附 missing_skills"
        print(f"  ✓ 空库自省:{len(abils)} 个能力全 sufficient=False(证据 gate)")
        assert len(os_.office.available_abilities()) == len(abils)
        print("  ✓ 各 Agent 一致(HKC 知识全局共享)")
        asyncio.run(os_.aclose())
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("  ✓ 能力自省成立:证据驱动、只读、无 LLM")


def test_ability_context_injection():
    print("\n【5】第2档 Step3:能力→上下文注入(opt-in;默认关不改行为;fail-safe)")
    from types import SimpleNamespace
    d = tempfile.mkdtemp(prefix="hkc_inject_")
    os_ = None
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        ag = os_.finance
        base = [{"role": "user", "content": "原始问题"}]

        assert ag._ability_augment("帮我做写作专家分析", list(base)) == base

        ag.ABILITY_AUGMENT = True
        assert ag._ability_augment("帮我做写作专家分析", list(base)) == base

        class _C:
            def list_abilities(self): return ["writing_expert"]
            def compile_ability(self, k):
                return SimpleNamespace(ability_key=k, display_name="写作专家",
                                       domain="writing", coverage={"修辞": 0.9}, skills=[])
            def search(self, q, mode="hybrid", top_k=10, domain=""):
                return [SimpleNamespace(name="修辞", summary="比喻与排比",
                                        ku_id="K1", score=0.9, ku_type="Concept")]
        os_.capabilities.register("hkc", SimpleNamespace(available=True, client=_C()))
        out = ag._ability_augment("帮我做写作专家风格分析", list(base))
        assert len(out) == 2 and "能力知识增强" in out[0]["content"] and "修辞" in out[0]["content"]
        assert out[1] == base[0], "原消息应保留在注入内容之后"
        print("  ✓ 默认关不注入;空库 no-op;启用+达标+命中→注入知识上下文")

        class _Cbad(_C):
            def search(self, *a, **k): raise RuntimeError("boom")
        os_.capabilities.register("hkc", SimpleNamespace(available=True, client=_Cbad()))
        assert ag._ability_augment("帮我做写作专家分析", list(base)) == base
        print("  ✓ 增强内部异常 → fail-safe 原样返回(不影响主流程)")
        asyncio.run(os_.aclose())
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("  ✓ 能力上下文注入成立:opt-in、匹配、fail-safe、默认行为不变")


def test_ability_as_skill_step4():
    print("\n【6】第2档 Step4:能力→SkillRegistry(证据 gate + read 经 PolicyGate + 可路由)")
    from types import SimpleNamespace
    from foxclaw.knowledge import register_abilities_as_skills
    from foxclaw.skills import SkillRegistry, SkillAdapter

    class _C:
        def list_abilities(self): return ["writing_expert", "quant_analyst"]
        def compile_ability(self, k):
            if k == "quant_analyst":
                raise RuntimeError("coverage 不足")
            return SimpleNamespace(ability_key=k, display_name="写作专家",
                                   domain="writing", coverage={"修辞": 0.9})
        def search(self, q, mode="hybrid", top_k=10, domain=""):
            return [SimpleNamespace(name="修辞", summary="比喻与排比",
                                    ku_id="K1", score=0.9, ku_type="Concept")]

    hkc = SimpleNamespace(available=True, client=_C())
    reg = SkillRegistry()
    ids = register_abilities_as_skills(reg, hkc)
    assert ids == ["ability:writing_expert"], ids
    assert reg.get("ability:quant_analyst") is None, "覆盖不足→不注册(证据 gate)"
    spec = reg.get("ability:writing_expert")
    assert spec is not None and spec.side_effect == "read", "能力 skill 为 read(无外部副作用)"

    res = SkillAdapter(reg).invoke(spec, {"query": "写作风格"})
    assert res.success and res.output.get("ability") == "writing_expert", res.to_dict()
    assert res.output["knowledge"] and res.output["knowledge"][0]["name"] == "修辞"
    print("  ✓ 证据 gate:达标能力注册为 read skill、覆盖不足不注册;经 PolicyGate 放行返回知识")

    assert register_abilities_as_skills(SkillRegistry(), None) == []
    assert register_abilities_as_skills(None, hkc) == []
    print("  ✓ HKC 不可用/registry 缺失 → 优雅返回 []")


def test_ability_skill_prune_and_concurrency():
    print("\n【7】深审修复:ability skill 陈旧清理(re-sync)+ SkillRegistry 线程安全")
    import threading
    from types import SimpleNamespace
    from foxclaw.knowledge import register_abilities_as_skills
    from foxclaw.skills import SkillRegistry, SkillSpec

    class _Cdrop:
        def __init__(self): self.ok = True
        def list_abilities(self): return ["writing_expert"]
        def compile_ability(self, k):
            if not self.ok:
                raise RuntimeError("coverage 掉了")
            return SimpleNamespace(ability_key=k, display_name="写作专家",
                                   domain="writing", coverage={})
        def search(self, *a, **k): return []
    c = _Cdrop()
    hkc = SimpleNamespace(available=True, client=c)
    reg = SkillRegistry()
    assert register_abilities_as_skills(reg, hkc) == ["ability:writing_expert"]
    assert reg.get("ability:writing_expert") is not None
    c.ok = False
    assert register_abilities_as_skills(reg, hkc) == []
    assert reg.get("ability:writing_expert") is None, "陈旧 ability skill 应在 re-sync 时被移除"
    print("  ✓ re-sync:覆盖掉了的 ability skill 被清理(不残留)")

    reg2 = SkillRegistry()
    errs = []
    def reg_worker():
        try:
            for i in range(400):
                reg2.register(SkillSpec(id=f"s{i % 20}", name="x", description="",
                                        kind="local", triggers=["x"]))
        except Exception as e:
            errs.append(repr(e))
    def read_worker():
        try:
            for _ in range(400):
                reg2.match("任意任务 x"); reg2.all(); reg2.catalog()
        except Exception as e:
            errs.append(repr(e))
    ts = [threading.Thread(target=reg_worker) for _ in range(3)] + \
         [threading.Thread(target=read_worker) for _ in range(3)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert not errs, f"并发 register/match 应无异常,实得 {errs[:2]}"
    print("  ✓ 6 线程并发 register/match/all/catalog 无异常(SkillRegistry 加锁生效)")


def main():
    print("=" * 60)
    print("知识核心 · HKC 融入(全离线,embedding=stub)")
    print("=" * 60)
    test_assemble_search_close()
    test_via_foxclawos_lazy_teardown()
    test_ability_readonly_surface()
    test_agent_available_abilities()
    test_ability_context_injection()
    test_ability_as_skill_step4()
    test_ability_skill_prune_and_concurrency()
    print("\n" + "=" * 60)
    print("✓ HKC 融入验证完成:装配/检索/stats + H1 资源修复 + 惰性/teardown")
    print("=" * 60)


if __name__ == "__main__":
    main()
