"""KEE 身份/去重:别名确定性匹配(「北平」aliases=['北京'] ↔「北京」归并)。
别名替代向量语义近重:零模型依赖、零余弦误合并;Fact/Claim 用完整 statement 守 exact。
无别名时退化为纯 exact,与旧版默认(关)行为一致。"""
import sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "vendor" / "hkc"))


def test_kee_alias_dedup():
    from hkc_kee.dedup import KnowledgeDeduplicator

    class FakeKU:
        def __init__(self, ku_id, ku_type, name, domain="", sources=None,
                     aliases=None, statement=None):
            self.ku_id = ku_id
            self.ku_type = types.SimpleNamespace(value=ku_type)
            self.name = name
            self.domain = domain
            self.sources = list(sources or [])
            self.aliases = list(aliases or [])
            self.statement = statement
            self.confidence = 0.5

    class FakeStore:
        def __init__(self): self.kus = {}
        def put(self, ku): self.kus[ku.ku_id] = ku
        def get(self, i): return self.kus.get(i)
        def query_all(self, limit=100000): return list(self.kus.values())
        def redirect_relations(self, a, b): return 0

    store = FakeStore(); d = KnowledgeDeduplicator(store)
    beijing = FakeKU("E1", "Entity", "北京", "", aliases=[])
    store.put(beijing)
    assert d.check_and_merge(beijing) is None, "首个应为新知识"
    beiping = FakeKU("E2", "Entity", "北平", "", aliases=["北京"], sources=["EVD_A"])
    store.put(beiping)
    assert d.check_and_merge(beiping) == "E1", "别名应命中 canonical「北京」"
    assert "EVD_A" in beijing.sources, "归并应把新证据并入存活 KU"
    print("[1] 别名正向归并 OK")

    store2 = FakeStore(); d2 = KnowledgeDeduplicator(store2)
    beiping2 = FakeKU("E1", "Entity", "北平", "", aliases=["北京"])
    store2.put(beiping2)
    assert d2.check_and_merge(beiping2) is None
    beijing2 = FakeKU("E2", "Entity", "北京", "", aliases=[])
    store2.put(beijing2)
    assert d2.check_and_merge(beijing2) == "E1", "新 KU 主名应命中已有 KU 的别名"
    print("[2] 别名反向归并 OK")

    store3 = FakeStore(); d3 = KnowledgeDeduplicator(store3)
    a = FakeKU("C1", "Concept", "value investing", "finance")
    store3.put(a)
    assert d3.check_and_merge(a) is None
    b = FakeKU("C2", "Concept", "value investing strategy", "finance")
    store3.put(b)
    assert d3.check_and_merge(b) is None, "无别名时不同名不应归并(纯 exact)"
    print("[3] 无别名纯 exact OK")

    store4 = FakeStore(); d4 = KnowledgeDeduplicator(store4)
    stmt1 = "A" * 80 + "X"
    stmt2 = "A" * 80 + "Y"
    f1 = FakeKU("F1", "Fact", stmt1[:80], statement=stmt1)
    store4.put(f1)
    assert d4.check_and_merge(f1) is None
    f2 = FakeKU("F2", "Fact", stmt2[:80], statement=stmt2)
    store4.put(f2)
    assert d4.check_and_merge(f2) is None, "前 80 字符相同但完整陈述不同,不应误并"
    print("[4] Fact 完整 statement 守 exact OK")

    store5 = FakeStore(); d5 = KnowledgeDeduplicator(store5)
    ent = FakeKU("E", "Entity", "北京", "", aliases=[])
    store5.put(ent)
    assert d5.check_and_merge(ent) is None
    con = FakeKU("C", "Concept", "北京", "地理")
    store5.put(con)
    assert d5.check_and_merge(con) is None, "Entity 与 Concept 同名不应归并(类型隔离)"
    print("[5] 类型隔离 OK")

    print("\nKEE 别名确定性去重验证通过")


if __name__ == "__main__":
    test_kee_alias_dedup()
