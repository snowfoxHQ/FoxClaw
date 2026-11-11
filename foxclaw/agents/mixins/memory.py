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
        """存储新记忆到 HMR。

        高敏感内容(`FOXCLAW_SENSITIVE_WRITE_MODE=enforce`)不会直接落库:记忆层把它转成
        待确认项并返回 pending_id,这里必须让用户看得见 —— 拦截后静默丢弃等于没说。
        """
        if _suppress_memory.get():
            return
        from foxclaw.core.tracing import span as _span
        with _span("memory.store", agent=self.AGENT_ID, type=mem_type):
            res = self.hmr.ingest(
                content,
                type=mem_type,
                agent_id=self.AGENT_ID,
                **kwargs,
            )
        pending_id = getattr(res, "pending_id", "")
        if pending_id:
            cats = "、".join(getattr(res, "categories", []) or []) or "高敏感"
            self._think(
                content=(f"这段内容涉及{cats},按规矩没有直接写进长期记忆。"
                         f"待确认项 {pending_id},你确认后我再落库。"),
                node_type="decision",
                conf=0.95,
            )
        return res
