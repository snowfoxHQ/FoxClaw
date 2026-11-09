"""证据融合语义化 —— 原始复盘点名的 `search/evidence.py "规则占位"`(此前一直漏修)。

修掉两个真问题:
  ① 否定词**子串**误命中:原 ("不是","并非","no longer","not") 按子串匹配 →
     "another"/"notebook" 里的 "not" 被命中 → 无关证据被判**矛盾**;而 confidence()
     按矛盾数扣分(0.1/条)→ 搜索置信被**幻影矛盾**无端拉低。
     这与 hkc_kee/kee.py 早已修过的 not⊂nothing / no⊂know 是**同一 bug 家族**,证据层漏了同步。
  ② 相似度是**字符级**(SequenceMatcher)而非语义 → 语义等价的证据识别不出互相支持。

无 ST 模型时优雅降级回字符级(不崩),故本测试对语义部分做"可用才断言"。
"""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "vendor" / "hkc"))


def test_evidence_semantic():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ["FOXCLAW_EVIDENCE_MODEL"] = "all-MiniLM-L6-v2"

    from foxclaw.search.evidence import EvidenceFusion, _has_neg, _semantic_sim
    from foxclaw.search.models import RawEvidence

    print("=" * 60); print("证据融合语义化验证"); print("=" * 60)
    f = EvidenceFusion()

    assert _has_neg("this is another way") is False, "another 里的 not 不该命中"
    assert _has_neg("the notebook is useful") is False, "notebook 里的 not 不该命中"
    assert _has_neg("I know this") is False, "know 里的 no 不该命中"
    assert _has_neg("cannot proceed") is True
    assert _has_neg("this is not good") is True
    assert _has_neg("这个方案并非最优") is True
    print("[1] 否定词词边界(another/notebook/know 不误命中)OK")

    assert f._contradicts("this is another way", "this is a way") is False, "幻影矛盾"
    assert f._contradicts("the notebook is useful", "the book is useful") is False, "幻影矛盾"
    print("[2] 幻影矛盾消失(another/notebook 不再被判矛盾)OK")

    assert f._contradicts("cannot proceed", "can proceed") is True
    assert f._contradicts("这个方案并非最优", "这个方案是最优") is True
    print("[3] 真矛盾仍识别(cannot/并非)OK")

    g = f.build([
        RawEvidence(id="e1", fact="this is another way", source="s1", origin="internal", confidence=0.9),
        RawEvidence(id="e2", fact="this is a way", source="s2", origin="internal", confidence=0.9),
    ])
    assert len(g.contradictions()) == 0, f"不该有矛盾边: {g.contradictions()}"
    assert f.confidence(g) >= 0.85, f"置信不该被幻影矛盾扣分: {f.confidence(g)}"
    print(f"[4] 端到端:无幻影矛盾边,置信未被误扣({f.confidence(g):.2f})OK")

    if _semantic_sim("a", "a") is None:
        print("[skip] ST 模型未缓存 → 语义部分跳过(已回退字符级,不崩)")
        print("\n证据融合语义化验证通过(降级路径)")
        if __name__ != "__main__":
            import pytest
            pytest.skip("ST 模型未缓存 → 语义部分跳过(降级路径已由 [1]-[4] 覆盖)",
                        allow_module_level=True)
        import sys as _sys
        _sys.exit(0)

    close = f._similarity("the plan is good", "the plan is nice")
    far = f._similarity("the plan is good", "the stock market crashed")
    assert close > 0.6 > far, f"近义应>0.6>无关: {close:.3f} vs {far:.3f}"
    assert f._similarity("hello world", "hello world") >= 0.99
    print(f"[5] 语义相似(近义 {close:.2f} > 0.6 > 无关 {far:.2f})OK")

    g2 = f.build([
        RawEvidence(id="a", fact="it will rain today", source="s1", origin="internal", confidence=0.8),
        RawEvidence(id="b", fact="rain is expected today", source="s2", origin="external", confidence=0.8),
    ])
    sup = [e for e in g2.edges if e.relation == "supports"]
    assert sup, "语义等价的证据应互相支持(字符级识别不出)"
    print("[6] 语义等价证据建立 supports 边 OK")

    g3 = f.build([
        RawEvidence(id="x", fact="hello world", source="s1", origin="internal", confidence=0.8),
        RawEvidence(id="y", fact="hello world", source="s2", origin="internal", confidence=0.8),
    ])
    assert len(g3.nodes) == 1, f"完全相同应去重: {list(g3.nodes)}"
    print("[7] 阈值在余弦下仍成立 + 相同证据去重 OK")

    print("\n证据融合语义化验证全部通过")


if __name__ == "__main__":
    test_evidence_semantic()
