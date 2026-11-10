"""
Retriever —— 内部检索(委托 InternalSearchEngine 串联 HMR/未来的 HKC)
与外部检索(联网搜索，当前是百度 AI 搜索)的统一入口。
"""
from __future__ import annotations
from .models import SubQuery, RawEvidence, RouteType
from .internal_search import InternalSearchEngine


class Retriever:
    def __init__(
        self,
        memory_port=None,
        knowledge_port=None,
        external_ports: list | None = None,
        internal_search: InternalSearchEngine | None = None,
    ):
        self.internal_search = internal_search or InternalSearchEngine(
            memory_port=memory_port, knowledge_port=knowledge_port,
        )
        self.external_ports = external_ports or []

    def retrieve(self, steps: list[SubQuery], route: RouteType) -> list[RawEvidence]:
        results: list[RawEvidence] = []
        for step in steps:
            if route in (RouteType.INTERNAL, RouteType.HYBRID):
                results += self.internal_search.search(step.text)
            if route in (RouteType.EXTERNAL, RouteType.HYBRID):
                results += self._retrieve_external(step)
        return results

    def _retrieve_external(self, step: SubQuery) -> list[RawEvidence]:
        out: list[RawEvidence] = []
        for port in self.external_ports:
            try:
                out += port.search(step.text)
            except Exception:
                continue
        return out
