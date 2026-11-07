"""
认知搜索内核(HSR 融入)测试 —— 全离线,不联网(external_ports=[])。

覆盖:
  1. 空源检索:无 HMR / 无知识 / 无外部端口 → 返回 SearchResult,迭代有上限,不死循环
  2. 内部记忆证据:假 HMR recall → 证据图含 MEMORY 来源节点
  3. 知识端口边界:HKCReadPort(client=None) 永远返回 [](降级不报错)
  4. 经 Foxclaw 惰性 search_runtime + aclose teardown

联网(百度)与真实 HKC 检索不在离线回归内;这里验证 HSR 的编排/融合/迭代/降级契约。
"""
import sys, tempfile, shutil, asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.search import SearchRuntime, HKCReadPort, SourceType


class FakeHMR:
    """假 HMR:recall 返回两条记忆,验证内部记忆证据能进证据图。"""
    def recall(self, query, agent_id=None, top_k=5):
        return [
            {"id": "m1", "content": f"关于「{query}」的历史记忆一", "confidence": 0.7},
            {"id": "m2", "content": f"关于「{query}」的历史记忆二", "confidence": 0.6},
        ]


def test_empty_sources_terminates():
    print("\n【1】空源检索:不死循环 + 迭代有上限")
    rt = SearchRuntime(hmr=None, knowledge_port=None, external_ports=[])
    r = rt.search("任意查询", agent_id="finance")
    assert r.iterations <= 5, f"迭代应有上限,实际 {r.iterations}"
    assert isinstance(r.trace, list) and r.trace
    assert 0.0 <= r.confidence <= 1.0
    print(f"  ✓ 返回 SearchResult: iterations={r.iterations} confidence={r.confidence:.2f} "
          f"trace={len(r.trace)}条")


def test_memory_evidence():
    print("\n【2】内部记忆证据进证据图(MEMORY 来源)")
    rt = SearchRuntime(hmr=FakeHMR(), knowledge_port=None, external_ports=[])
    # "分析" 是 HYBRID 触发词 → 会查内部记忆(见 planner._HYBRID_HINTS)
    r = rt.search("分析活字印刷的历史", agent_id="office")
    nodes = list(r.graph.nodes.values())
    mem = [n for n in nodes if n.source == SourceType.MEMORY]
    assert len(mem) >= 1, "假 HMR 的记忆应作为证据进图"
    print(f"  ✓ 证据图 {len(nodes)} 节点,其中 MEMORY 来源 {len(mem)} 个")
    print(f"    样例: {mem[0].fact[:30]!r} conf={mem[0].confidence:.2f}")


def test_hkc_port_degrades():
    print("\n【3】知识端口降级:HKCReadPort(client=None) 返回 []")
    port = HKCReadPort(client=None)
    assert port.available is False
    assert port.query("任意", top_k=5) == []
    print("  ✓ client=None 时 available=False 且 query 返回 [](不报错)")


def test_via_foxclawos_lazy():
    print("\n【4】经 Foxclaw 惰性 search_runtime + aclose")
    d = tempfile.mkdtemp(prefix="hsr_os_")
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        # 【P1-3】search_runtime 由能力中枢惰性构造:未访问前未缓存
        assert "search_runtime" not in os_.capabilities._caps, "未访问前不该构造"
        rt = os_.search_runtime   # 触发构造(顺带惰性装配 HKC 作知识端口)
        assert rt is not None and os_.search_runtime is rt, "应缓存同一实例"
        # 不联网:替换外部端口为空,跑一次
        rt.external_ports = []
        r = rt.search("测试查询", agent_id="supervisor")
        assert r.confidence is not None
        print(f"  ✓ 惰性构造成功,search 返回 confidence={r.confidence:.2f}")
        asyncio.run(os_.aclose())
        print("  ✓ aclose() 释放(含惰性装配过的 HKC)")
    finally:
        shutil.rmtree(d, ignore_errors=True)


class _NS:
    """简易命名空间对象(带属性)。"""
    def __init__(self, **kw): self.__dict__.update(kw)


class FakeThoughtChain:
    def search_nodes(self, keyword, agent_id=None, limit=20):
        if "预算" in keyword or "风险" in keyword:
            return [_NS(id="t1", agent_id="finance", content="评估本月预算风险偏高")]
        return []


class FakeRegistry:
    def catalog(self):
        return [{"name": "gmail_send", "triggers": ["发邮件", "email"],
                 "kind": "mcp", "side_effect": "external"}]


def test_agent_port_all_subsources():
    print("\n【5】AgentSourcePort:四类子源全连通")
    from foxclaw.search import AgentSourcePort
    finance = _NS(
        transactions=[_NS(entity="星巴克咖啡", amount=35, category="餐饮", tags=["coffee"])],
        budgets=[_NS(category="餐饮", monthly_limit=800)], entities={})
    learning = _NS(items={"k1": _NS(content="活字印刷术由毕昇发明", topic="历史", tags=[])})
    itinerary = _NS(waypoints=[_NS(name="首都机场", address="北京朝阳", city="北京", start_time="09:00")])
    agents = {"finance": _NS(get_status=lambda: {"role": "理财风控", "status": "idle"})}

    class HMRStub:
        def recall(self, query, agent_id=None, top_k=5):
            return [{"id": "m1", "content": f"{agent_id}记得关于{query}的事"}]

    port = AgentSourcePort(agents=agents, finance_runtime=finance, learning_runtime=learning,
                           itinerary_runtime=itinerary, thought_chain=FakeThoughtChain(),
                           skill_registry=FakeRegistry(), hmr=HMRStub())
    assert port.available
    from foxclaw.search import SourceType

    def origins(q):
        return {e.origin for e in port.query(q, top_k=8)}

    assert any("finance:transaction" in o for o in origins("星巴克")), "财务交易应连通"
    assert any("learning" in o for o in origins("活字印刷")), "学习知识应连通"
    assert any("itinerary" in o for o in origins("首都机场")), "行程点应连通"
    assert any("thoughtchain" in o for o in origins("预算风险")), "推理链应连通"
    assert any("capability:skill" in o for o in origins("发邮件")), "能力目录应连通"
    assert any(":hmr:" in o for o in origins("任意")), "各Agent记忆应连通"
    # 全部证据都标记为 AGENT 源
    evs = port.query("星巴克", top_k=8)
    assert all(e.source == SourceType.AGENT for e in evs)
    print("  ✓ 财务/学习/行程/推理链/能力/各Agent记忆 六路均连通,来源标记 AGENT")


def test_bidirectional_and_internal_only():
    print("\n【6】双向:Agent 调用内部搜索(internal_only,不联网)")
    d = tempfile.mkdtemp(prefix="hsr_bi_")
    try:
        from foxclaw import Foxclaw
        from foxclaw.search import SourceType
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        # 往财务内核放一笔交易
        from foxclaw.finance.models import Transaction
        os_.finance_runtime.add_transaction(
            Transaction(entity="蔚来汽车", amount=-260000, category="购车",
                        account="alipay", timestamp="2026-05-01T10:00:00"),
            run_gatekeeper=False)
        # 触发 search_runtime 装配(会把自身注入各 Agent)
        _ = os_.search_runtime
        # 财务 Agent 反向调用内部搜索
        res = os_.finance.search_internal("蔚来汽车")
        assert res is not None, "search_internal 应返回 SearchResult"
        agent_ev = [n for n in res.graph.nodes.values() if n.source == SourceType.AGENT]
        assert any("蔚来" in n.fact for n in agent_ev), "应从财务内核检索到该交易"
        # internal_only:轨迹里不应出现 web 证据来源
        assert all(n.source != SourceType.WEB for n in res.graph.nodes.values())
        print(f"  ✓ os_.finance.search_internal('蔚来汽车') → AGENT证据 {len(agent_ev)} 条,无联网")
        asyncio.run(os_.aclose())
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_internal_source_isolation():
    print("\n【7】深审修复:内部单源抛异常被隔离(不掀翻整个 search)")
    class _BoomPort:
        def query(self, text, top_k=5): raise RuntimeError("内部源炸了")
    class _OKKnow:
        def query(self, text, top_k=5):
            from foxclaw.search import SourceType
            from foxclaw.search.models import RawEvidence
            return [RawEvidence(id="k1", fact=f"知识:{text}", source=SourceType.KNOWLEDGE,
                                origin="hkc:k1", confidence=0.7)]
    # agent_port 抛异常 + memory(FakeHMR)正常 + knowledge 正常 → search 不崩,仍返回其它源证据
    rt = SearchRuntime(hmr=FakeHMR(), knowledge_port=_OKKnow(),
                       agent_port=_BoomPort(), external_ports=[])
    r = rt.search("测试查询", agent_id="finance", internal_only=True)
    facts = [n.fact for n in r.graph.nodes.values()]
    assert any("知识" in f for f in facts), "知识源应仍返回(不受 agent 源崩溃影响)"
    assert any("记忆" in f for f in facts), "记忆源应仍返回"
    print(f"  ✓ agent 源抛异常被隔离,其它源正常:{len(r.graph.nodes)} 节点")
    # 全部内部源都抛异常 → search 仍不崩,返回空图(优雅降级)
    rt2 = SearchRuntime(hmr=None, knowledge_port=_BoomPort(),
                        agent_port=_BoomPort(), external_ports=[])
    r2 = rt2.search("测试", internal_only=True)
    assert len(r2.graph.nodes) == 0 and 0.0 <= r2.confidence <= 1.0
    print("  ✓ 全源崩溃 → search 不崩、返回空图(与 external 隔离对称)")


def main():
    print("=" * 60)
    print("认知搜索内核 · HSR 融入(全离线,不联网)")
    print("=" * 60)
    test_empty_sources_terminates()
    test_memory_evidence()
    test_hkc_port_degrades()
    test_via_foxclawos_lazy()
    test_agent_port_all_subsources()
    test_bidirectional_and_internal_only()
    test_internal_source_isolation()
    print("\n" + "=" * 60)
    print("✓ HSR 融入验证完成:编排/证据融合/迭代上限/端口降级/惰性teardown")
    print("=" * 60)


if __name__ == "__main__":
    main()
