"""真语义检索 + KEE 语义化验证。用 HMR 已缓存的 all-MiniLM-L6-v2 证明机制(离线可用);
模型未缓存则优雅跳过(退出0,不 FAIL)——真机上装了多语言模型即走真语义,否则 auto/keyword 降级。"""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "vendor" / "hkc"))

def test_semantic():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    MODEL = "all-MiniLM-L6-v2"   # HMR 在用,进程缓存里通常有;保证离线可测
    from hkc_search.embedding_backends import get_st_model

    print("=" * 60); print("真语义检索 + KEE 语义化验证"); print("=" * 60)

    # [0] 否定词不再子串误命中(不依赖模型,总能测):badge⊅bad-word, know⊅no-word
    from hkc_kee.kee import _SimpleEmbedder as _SE
    assert _SE._has_neg("this is a badge") is False, "badge 误命中否定"
    assert _SE._has_neg("I know this") is False, "know 误命中否定"
    assert _SE._has_neg("this is not good") is True
    assert _SE._has_neg("cannot proceed") is True
    assert _SE._has_neg("这个方案不好") is True
    print("[0] KEE 否定词词边界(badge/know 不误命中) OK")

    # [0b] 语义方向阈值接线:默认 0.30(与既有硬编码一致,behavior-preserving)+ 可经构造参数配置
    from hkc_kee.kee import KnowledgeEvolutionEngine
    class _Bus:
        def subscribe(self, *a, **k): pass
    class _Store: pass
    class _Gen: pass
    eng = KnowledgeEvolutionEngine(_Store(), _Bus(), _Gen())
    assert abs(eng.similarity_threshold - 0.30) < 1e-9, "默认阈值应 0.30(与既有硬编码一致)"
    eng2 = KnowledgeEvolutionEngine(_Store(), _Bus(), _Gen(), similarity_threshold=0.90)
    assert abs(eng2.similarity_threshold - 0.90) < 1e-9, "阈值应可经构造参数配置"
    print("[0b] KEE 语义方向阈值接线(默认 0.30 / 可配置) OK")

    m = get_st_model(MODEL)
    if m is None:
        print("[skip] all-MiniLM 未缓存,跳过真语义验证(生产靠 auto/keyword 降级兜底)")
        # 两种运行方式都要正确退出(M7):
        #   · 被 pytest 收集 → pytest.skip(allow_module_level) 是标准做法
        #   · 被 run_all.py 当脚本直跑 → pytest.skip 抛 Skipped 会让脚本非零退出,
        #     反而把「跳过」变成「失败」。故按 __name__ 区分。
        if __name__ != "__main__":
            import pytest
            pytest.skip("all-MiniLM 未缓存 → 真语义验证跳过(auto/keyword 降级兜底)",
                        allow_module_level=True)
        sys.exit(0)

    # 1) 共享缓存单例:同名只加载一次
    assert get_st_model(MODEL) is m, "同名应共享同一实例(单例)"
    print("[1] get_st_model 同名单例 OK")

    # 2) LocalSTBackend 真余弦:相近 > 无关
    import numpy as np
    from hkc_search.embedding_backends import LocalSTBackend
    b = LocalSTBackend(model_name=MODEL)
    va, vb, vc = b.encode(["I am very happy today", "Feeling great today", "The stock market crashed"])
    sim_close = float(np.dot(va, vb)); sim_far = float(np.dot(va, vc))
    assert sim_close > sim_far, f"相近应更高: {sim_close} vs {sim_far}"
    print(f"[2] LocalST 真余弦:相近({sim_close:.2f}) > 无关({sim_far:.2f}) OK")

    # 3) KEE _SimpleEmbedder 语义方向(指向缓存模型)
    from hkc_kee.kee import _SimpleEmbedder
    _SimpleEmbedder._MODEL_NAME = MODEL
    emb = _SimpleEmbedder()
    s_same = emb.score("this plan is good", "this plan is nice")
    s_unrel = emb.score("this plan is good", "it will rain tomorrow")
    s_contra = emb.score("this plan is good", "this plan is not good")
    assert s_same > s_unrel, f"相近应 > 无关: {s_same} vs {s_unrel}"
    assert s_contra < s_same, f"矛盾应 < 同向: {s_contra} vs {s_same}"
    print(f"[3] KEE score:同向({s_same:.2f}) > 无关({s_unrel:.2f}),矛盾({s_contra:.2f})<同向 OK")

    print("\n真语义检索 + KEE 语义化验证通过")

if __name__ == "__main__":
    test_semantic()
