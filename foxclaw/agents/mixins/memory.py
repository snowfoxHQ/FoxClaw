"""MemoryMixin:HMR 记忆召回与写入。

从 `foxclaw/agents/base.py` 搬移出来的记忆相关方法。
"""
from typing import Any, Callable

from ._context import _suppress_memory


class MemoryMixin:
    AGENT_ID: str
    hmr: Any
    _think: Callable[..., Any]

    def _recall_memory(self, query: str, top_k: int = 5) -> list:
        """从 HMR 召回相关记忆，记录到 ThoughtChain"""
        from foxclaw.core.tracing import span as _span
        with _span("memory.recall", agent=self.AGENT_ID, top_k=top_k):
            results = self.hmr.recall(query=query, agent_id=self.AGENT_ID, top_k=top_k)
        if results:
            mem_ids = [r.get("id", "") for r in results]
            self._think(
                content=f"Recalled {len(results)} memories for: {query}",
                node_type="memory_retrieval",
                conf=0.95,
                mem_refs=mem_ids,
            )
        return results

    def _store_memory(self, content: str, mem_type: str = "concept", **kwargs):
        """存储新记忆到 HMR"""
        if _suppress_memory.get():
            return
        from foxclaw.core.tracing import span as _span
        with _span("memory.store", agent=self.AGENT_ID, type=mem_type):
            self.hmr.ingest(
                content,
                type=mem_type,
                agent_id=self.AGENT_ID,
                **kwargs,
            )
