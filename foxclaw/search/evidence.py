"""
Evidence Fusion —— 把离散、可能重复/冲突的原始证据，
构建成一张有 support/contradict/grounded_by 关系的证据图，并算出整体置信度。

相似度/矛盾判断已**语义化**(2026-07-16),与 KEE 冲突检测同款、共用同一份 ST 模型与向量缓存:
  · 相似度:sentence-transformers 余弦;无 ST/模型未缓存 → 回退 SequenceMatcher(降级不崩)
  · 矛盾:否定词方向 —— ASCII 按**词边界**、中文按子串
对外接口(build / confidence)保持不变。

修掉的两个真问题(原为"规则占位"):
  ① 否定词子串误命中:原 ("不是","并非","no longer","not") 是子串匹配 →
     "another"/"notebook" 里的 "not" 被命中 → 无关证据被判**矛盾** → 而 confidence()
     按矛盾数扣分(0.1/条)→ 搜索置信被**幻影矛盾**无端拉低。
     (与 hkc_kee/kee.py 早已修过的 not⊂nothing / no⊂know 是同一 bug 家族,此处漏同步。)
  ② 字符级相似度非语义:「机器学习很有用」vs「ML 非常有价值」=0.13、
     「今天下雨」vs「今日降雨」=0.50(<0.6)→ 语义等价的证据**识别不出互相支持**。
"""
from __future__ import annotations
import os
import re
from difflib import SequenceMatcher
from .models import RawEvidence, EvidenceGraph, EvidenceNode, EvidenceEdge

_SIMILARITY_SUPPORT = 0.6
_SIMILARITY_DUP = 0.92

_NEG_ASCII = ("not", "no longer", "never", "cannot", "isn't", "aren't", "won't")
_NEG_CJK = ("不是", "并非", "并不", "没有", "无法", "不会", "未能")
_NEG_ASCII_RE = re.compile(
    r"\b(" + "|".join(re.escape(n) for n in sorted(_NEG_ASCII, key=len, reverse=True)) + r")\b")


def _has_neg(text: str) -> bool:
    t = (text or "").lower()
    return bool(_NEG_ASCII_RE.search(t)) or any(n in t for n in _NEG_CJK)


def _sem_model_name() -> str:
    return (os.environ.get("FOXCLAW_EVIDENCE_MODEL")
            or os.environ.get("EMBEDDING_MODEL")
            or os.environ.get("HKC_EMBEDDING_MODEL")
            or os.environ.get("HKC_KEE_SEMANTIC_MODEL")
            or "paraphrase-multilingual-MiniLM-L12-v2")


def _semantic_sim(a: str, b: str):
    """ST 余弦 ∈ [0,1](负相关截到 0)。取不到模型 → None(调用方回退字符级)。
    走 HKC 共享向量缓存 → 同文本全局只编码一次(与检索/KEE/去重同一份)。"""
    try:
        from hkc_search.embedding_backends import get_st_model, encode_cached
        name = _sem_model_name()
        model = get_st_model(name)
        if model is None:
            return None
        import numpy as np
        va, vb = encode_cached(model, name, [a or "", b or ""])
        return max(0.0, min(1.0, float(np.dot(va, vb))))
    except Exception:
        return None


class EvidenceFusion:
    def build(self, raw_list: list[RawEvidence]) -> EvidenceGraph:
        graph = EvidenceGraph()

        for e in self._dedup(raw_list):
            graph.add_node(EvidenceNode(
                id=e.id, fact=e.fact, source=e.source,
                origin=e.origin, confidence=e.confidence,
                grounded_by=e.grounded_by,
            ))

        nodes = list(graph.nodes.values())
        for i, a in enumerate(nodes):
            for b in nodes[i + 1:]:
                sim = self._similarity(a.fact, b.fact)
                if sim >= _SIMILARITY_SUPPORT:
                    graph.add_edge(EvidenceEdge(a.id, b.id, "supports"))
                elif self._contradicts(a.fact, b.fact):
                    graph.add_edge(EvidenceEdge(a.id, b.id, "contradicts"))

        for node in nodes:
            if node.grounded_by and node.grounded_by in graph.nodes:
                graph.add_edge(EvidenceEdge(node.id, node.grounded_by, "grounded_by"))

        return graph

    def confidence(self, graph: EvidenceGraph) -> float:
        if not graph.nodes:
            return 0.0
        base = sum(n.confidence for n in graph.nodes.values()) / len(graph.nodes)
        penalty = 0.1 * len(graph.contradictions())
        return max(0.0, min(1.0, base - penalty))

    def _dedup(self, raw_list: list[RawEvidence]) -> list[RawEvidence]:
        kept: list[RawEvidence] = []
        for e in raw_list:
            if not any(self._similarity(e.fact, k.fact) >= _SIMILARITY_DUP for k in kept):
                kept.append(e)
        return kept

    def _similarity(self, a: str, b: str) -> float:
        """语义余弦优先;无 ST/加载失败 → 回退字符级 SequenceMatcher(降级不崩)。"""
        v = _semantic_sim(a, b)
        return v if v is not None else SequenceMatcher(None, a, b).ratio()

    def _contradicts(self, a: str, b: str) -> bool:
        """主题相近但**否定方向相反** → 矛盾。否定词按词边界判,不再被 another/notebook 误命中。"""
        return _has_neg(a) != _has_neg(b) and self._similarity(a, b) >= 0.4
