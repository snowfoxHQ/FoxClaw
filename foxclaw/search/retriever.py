"""
Retriever —— 内部检索(委托 InternalSearchEngine 串联 HMR / HKC)的统一入口。

外部端口只是可插拔扩展点,不内置任何联网实现:external_ports 为空时,
EXTERNAL / HYBRID 路由只返回内部证据。
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
