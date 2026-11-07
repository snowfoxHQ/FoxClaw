"""Harness 三个检测维度：语义距离 / 工具意图 / 范围蔓延。"""
import hashlib

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from ._base import TOOL_CATEGORIES


# 文本向量由 foxclaw.capability.embedding_service 统一提供(进程内共享,同名只加载一次)。
# 不可用时该服务返回 None,SemanticDrift 自动降级为 n-gram 哈希嵌入。


class SemanticDrift:
    """维度一：语义距离。优先 sentence-transformers,不可用则降级 n-gram 哈希嵌入。"""

    DIM = 256

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        # P0:走进程内共享 EmbeddingService,同名模型全系统只加载一次
        # (原来每个 Harness 各 load 一份;5 Agent = 5 份 → 现 1 份共享,只读并发 encode 安全)。
        from ..capability.embedding_service import get_text_embedder
        self._model = get_text_embedder(model_name)
        self._mode = "transformer" if self._model is not None else "hash"

    @property
    def mode(self) -> str:
        return self._mode

    def _encode(self, text: str) -> np.ndarray:
        if self._mode == "transformer":
            return self._model.encode(text)
        return self._hash_embed(text)

    def _hash_embed(self, text: str) -> np.ndarray:
        """字符 3-gram 哈希到固定维向量。余弦相似度数学真实,只是不带语义模型。"""
        vec = np.zeros(self.DIM, dtype=np.float32)
        text = (text or "").lower().strip()
        if not text:
            return vec
        grams = [text[i:i+3] for i in range(max(len(text) - 2, 1))]
        for g in grams:
            h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
            idx = h % self.DIM
            sign = 1.0 if (h // self.DIM) % 2 == 0 else -1.0
            vec[idx] += sign
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    def encode(self, text: str) -> np.ndarray:
        """把文本编码成向量(纯函数,无副作用)。每任务基线自持此向量,并发安全。"""
        return self._encode(text)

    def score(self, content: str, baseline_vec: np.ndarray) -> float:
        """内容相对**每任务基线向量**的语义漂移。baseline_vec 由调用方(check)显式传入,
        不再用共享的实例级基线 → 并发顶层任务的语义基线互不覆盖。"""
        if baseline_vec is None:
            return 0.0
        node_vec = self._encode(content)
        sim = cosine_similarity([baseline_vec], [node_vec])[0][0]
        # 余弦相似度范围 [-1, 1],哈希嵌入带符号累加可能产生负相似度,
        # 1 - sim 会超过 1.0 → 必须双向裁剪到 [0, 1],否则漂移分超出阈值范围使判定失真。
        return float(min(1.0, max(0.0, 1.0 - sim)))


class ToolIntentDrift:
    """维度二：工具意图"""

    def score(self, tool_name: str, allowed_categories: set[str]) -> float:
        if not tool_name:
            return 0.0
        for cat, patterns in TOOL_CATEGORIES.items():
            if any(tool_name.startswith(p) or p in tool_name for p in patterns):
                return 0.0 if cat in allowed_categories else 1.0
        return 0.3  # 未知工具：中等风险


class ScopeCreepDrift:
    """维度三：范围蔓延"""

    def score(self, node_entities: set[str], baseline_entities: set[str]) -> float:
        # 空基线返回 0 是有意设计:范围蔓延是"相对基线的偏离",没有基线参照就无从
        # 谈蔓延。若此处对空基线返回高分,会让所有尚未建立实体基线的早期任务被误判为
        # 范围越界(误报)。范围检测的有效性依赖基线实体提取——若该维度长期为 0,
        # 应排查实体提取(register_goal 的 entities)是否为空,而非改动本函数。
        if not baseline_entities:
            return 0.0
        new_entities = node_entities - baseline_entities
        return min(len(new_entities) / max(len(baseline_entities), 1), 1.0)
