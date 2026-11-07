"""
foxclaw.capability —— 全系统共享的能力层(Capability Plane)。

把"重、且应当全进程共享一份"的能力收敛到这里,避免各模块各自加载:
  · EmbeddingService:sentence-transformers 文本向量,按模型名缓存,全进程只加载一次
    (此前 5 个 Harness 各建一个 SemanticDrift → 各自 load 一次模型,启动 6×→现 1×)

(后续 v2 的 LLMRouter 也应上浮到这一层,与 EmbeddingService 并列。)
"""
from .embedding_service import get_text_embedder, embedding_stats

__all__ = ["get_text_embedder", "embedding_stats"]
