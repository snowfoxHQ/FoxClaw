"""
Foxclaw 学习子系统 — 知识图谱(连接层)
把分散的知识点串成网。三种边:
  manual(手动标注)、same_topic(同主题/标签自动)、semantic(语义相近自动)
核心思想:学习不只是记住单点,更是发现知识之间的连接。
"""

import hashlib
from collections import defaultdict
from .models import KnowledgeItem, KnowledgeEdge


class KnowledgeGraph:
    """轻量知识图谱。语义相似度优先用外部 embedder(HMR),
    不可用时降级为字符 n-gram 哈希向量余弦(与 Harness 同思路)。"""

    DIM = 256

    def __init__(self, embedder=None):
        self.items: dict = {}
        self.edges: list = []
        self._embedder = embedder

    def add_item(self, item: KnowledgeItem):
        self.items[item.id] = item

    def remove_item(self, item_id: str):
        """移除一个知识点及其相关的所有边。"""
        self.items.pop(item_id, None)
        self.edges = [e for e in self.edges
                      if e.source_id != item_id and e.target_id != item_id]

    def link_manual(self, source_id: str, target_id: str, reason: str = ""):
        self.edges.append(KnowledgeEdge(
            source_id=source_id, target_id=target_id,
            edge_type="manual", weight=1.0, reason=reason or "手动关联"))

    def auto_link_by_topic(self):
        """同主题 / 共享标签的知识点自动连接。"""
        ids = list(self.items.keys())
        new_edges = 0
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = self.items[ids[i]], self.items[ids[j]]
                shared_tags = set(a.tags) & set(b.tags)
                if a.topic == b.topic and a.topic != "general":
                    self.edges.append(KnowledgeEdge(
                        source_id=a.id, target_id=b.id, edge_type="same_topic",
                        weight=0.7, reason=f"同主题:{a.topic}"))
                    new_edges += 1
                elif shared_tags:
                    self.edges.append(KnowledgeEdge(
                        source_id=a.id, target_id=b.id, edge_type="same_topic",
                        weight=0.5, reason=f"共享标签:{','.join(shared_tags)}"))
                    new_edges += 1
        return new_edges

    def auto_link_by_semantic(self, threshold: float = 0.55):
        """语义相近的知识点自动连接(余弦相似度)。"""
        ids = list(self.items.keys())
        vecs = {i: self._embed(self.items[i].content) for i in ids}
        new_edges = 0
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                sim = self._cosine(vecs[ids[i]], vecs[ids[j]])
                if sim >= threshold:
                    self.edges.append(KnowledgeEdge(
                        source_id=ids[i], target_id=ids[j], edge_type="semantic",
                        weight=round(sim, 3),
                        reason=f"语义相近({sim:.2f})"))
                    new_edges += 1
        return new_edges

    def neighbors(self, item_id: str) -> list:
        """某知识点的所有关联点。"""
        out = []
        for e in self.edges:
            if e.source_id == item_id:
                out.append((e.target_id, e.edge_type, e.weight, e.reason))
            elif e.target_id == item_id:
                out.append((e.source_id, e.edge_type, e.weight, e.reason))
        return sorted(out, key=lambda x: -x[2])

    def clusters(self) -> list:
        """连通分量 = 知识簇。返回每个簇的成员 id 列表。"""
        adj = defaultdict(set)
        for e in self.edges:
            adj[e.source_id].add(e.target_id)
            adj[e.target_id].add(e.source_id)
        seen, result = set(), []
        for node in self.items:
            if node in seen:
                continue
            stack, comp = [node], []
            while stack:
                n = stack.pop()
                if n in seen:
                    continue
                seen.add(n)
                comp.append(n)
                stack.extend(adj[n] - seen)
            result.append(comp)
        return result

    def stats(self) -> dict:
        type_count: defaultdict[str, int] = defaultdict(int)
        for e in self.edges:
            type_count[e.edge_type] += 1
        return {
            "items": len(self.items),
            "edges": len(self.edges),
            "edge_types": dict(type_count),
            "clusters": len(self.clusters()),
        }

    def _embed(self, text: str):
        if self._embedder is not None:
            try:
                return self._embedder(text)
            except Exception:
                pass
        return self._hash_embed(text)

    def _hash_embed(self, text: str):
        import math
        vec = [0.0] * self.DIM
        text = (text or "").lower().strip()
        grams = [text[i:i+3] for i in range(max(len(text) - 2, 1))]
        for g in grams:
            h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
            idx = h % self.DIM
            vec[idx] += 1.0 if (h // self.DIM) % 2 == 0 else -1.0
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm > 0 else vec

    @staticmethod
    def _cosine(a, b) -> float:
        import math
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        if na == 0 or nb == 0:
            return 0.0
        return max(0.0, min(1.0, dot / (na * nb)))
