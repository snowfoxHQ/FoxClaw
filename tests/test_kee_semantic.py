"""KEE 身份/去重向量化:规则指纹之上加语义近重层(不同表达同一身份 → 归并)。
opt-in(HKC_KEE_SEMANTIC=1)、仅身份型(Entity/Concept)、Fact/Claim 守 exact。
用进程缓存的 all-MiniLM-L6-v2 证明机制;未缓存则优雅跳过(退出0)。"""
import os, sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "vendor" / "hkc"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

MODEL = "all-MiniLM-L6-v2"
from hkc_search.embedding_backends import get_st_model
from hkc_kee.dedup import KnowledgeDeduplicator

print("=" * 60); print("KEE 身份/去重向量化验证"); print("=" * 60)


class FakeKU:
    def __init__(self, ku_id, ku_type, name, domain="", sources=None):
        self.ku_id = ku_id
        self.ku_type = types.SimpleNamespace(value=ku_type)
        self.name = name
        self.domain = domain
        self.sources = list(sources or [])
        self.confidence = 0.5


class FakeStore:
    def __init__(self): self.kus = {}
    def put(self, ku): self.kus[ku.ku_id] = ku
    def get(self, i): return self.kus.get(i)
    def query_all(self, limit=100000): return list(self.kus.values())
    def redirect_relations(self, a, b): return 0


m = get_st_model(MODEL)
if m is None:
    print("[skip] all-MiniLM 未缓存,跳过语义去重验证(生产靠 exact-only 降级兜底)")
    # 两种运行方式都要正确退出(M7):pytest 收集 → pytest.skip;
    # run_all.py 当脚本直跑 → pytest.skip 抛 Skipped 会变成非零退出(把跳过当失败)。
    if __name__ != "__main__":
        import pytest
        pytest.skip("all-MiniLM 未缓存 → 语义去重验证跳过(exact-only 降级兜底)",
                    allow_module_level=True)
    sys.exit(0)

# 用真实余弦定阈值(取近/远的中点),避免硬编码模型分数导致脆弱
import numpy as np
va, vb, vc = m.encode(["value investing", "value investing strategy", "quantum computing"],
                      normalize_embeddings=True)
s_close = float(np.dot(va, vb)); s_far = float(np.dot(va, vc))
assert s_close > s_far, (s_close, s_far)
os.environ["HKC_KEE_SEMANTIC_MODEL"] = MODEL
os.environ["HKC_KEE_SEMANTIC_THRESHOLD"] = str((s_close + s_far) / 2)
print(f"[0] 阈值取近({s_close:.2f})/远({s_far:.2f})中点 OK")

# [1] 开启语义:身份型近义 → 归并;无关 → 不并
os.environ["HKC_KEE_SEMANTIC"] = "1"
store = FakeStore(); d = KnowledgeDeduplicator(store)
k1 = FakeKU("KU1", "Concept", "value investing", "finance"); store.put(k1)
assert d.check_and_merge(k1) is None, "首个应为新知识"
k2 = FakeKU("KU2", "Concept", "value investing strategy", "finance", sources=["EVD_A"]); store.put(k2)
assert d.check_and_merge(k2) == "KU1", "近义身份应语义归并入 KU1"
assert "EVD_A" in k1.sources, "归并应把新证据并入存活 KU"
k3 = FakeKU("KU3", "Concept", "quantum computing", "finance"); store.put(k3)
assert d.check_and_merge(k3) is None, "无关概念不应被误并"
print("[1] 语义近重:近义归并(证据并入)+ 无关不误并 OK")

# [2] 别名学习:归并过的名字以后走 exact 快路径 → 直接命中 canonical
k2b = FakeKU("KU2b", "Concept", "value investing strategy", "finance"); store.put(k2b)
assert d.check_and_merge(k2b) == "KU1", "同名应经 exact 快路径命中 canonical"
print("[2] 别名学习(归并名→exact 快路径)OK")

# [3] 类型安全:Fact(陈述)不做语义归并,即便近义也守 exact(避免误并矛盾陈述)
store3 = FakeStore(); d3 = KnowledgeDeduplicator(store3)
f1 = FakeKU("F1", "Fact", "the earth is round", "science"); store3.put(f1)
assert d3.check_and_merge(f1) is None
f2 = FakeKU("F2", "Fact", "the earth is spherical", "science"); store3.put(f2)
assert d3.check_and_merge(f2) is None, "Fact 应守 exact,不语义归并"
print("[3] 类型安全:Fact 守 exact(不语义归并)OK")

# [4] 默认关(behavior-preserving):不设 HKC_KEE_SEMANTIC → 近义身份也不并
os.environ.pop("HKC_KEE_SEMANTIC", None)
store4 = FakeStore(); d4 = KnowledgeDeduplicator(store4)
a1 = FakeKU("A1", "Concept", "value investing", "finance"); store4.put(a1)
assert d4.check_and_merge(a1) is None
a2 = FakeKU("A2", "Concept", "value investing strategy", "finance"); store4.put(a2)
assert d4.check_and_merge(a2) is None, "默认关时应退回 exact-only,不并近义"
print("[4] 默认关(opt-in,behavior-preserving)OK")

print("\nKEE 身份/去重向量化验证通过")
