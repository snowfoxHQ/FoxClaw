"""
BaiduWebSearchPort —— 把已有的 foxclaw.core.web_search（百度 AI 搜索）
包装成 HSR 的外部检索端口(ExternalSearchPort)，接入 Retriever。

没有另起一套联网搜索实现：Foxclaw 已经在用百度 AI 搜索
(foxclaw/core/web_search.py，Agent 的 `_web_augment` 也在用它)，
这里只是把它的返回结果(answer + references)转成 RawEvidence，
让 HSR 的证据融合/策略循环可以复用它，而不是绕开它另建一套。

未配置 BAIDU_SEARCH_KEY 或网络不通时，底层 web_search.search_answer()
本身就会静默返回空 dict，这里顺势返回空列表，不影响 HSR 主流程。
"""
from __future__ import annotations
from ..core import web_search as _ws
from .models import RawEvidence, SourceType


class BaiduWebSearchPort:
    name = "baidu_web_search"

    def search(self, text: str, top_k: int = 5) -> list[RawEvidence]:
        payload = _ws.search_answer(text)
        if not payload:
            return []

        out: list[RawEvidence] = []

        answer = payload.get("answer", "")
        if answer:
            out.append(RawEvidence(
                id="baidu:answer",
                fact=answer,
                source=SourceType.WEB,
                origin="baidu_ai_search",
                confidence=0.65,
            ))

        for i, ref in enumerate(payload.get("references", [])[:top_k]):
            snippet = ref.get("content", "") or ref.get("abstract", "")
            if not snippet:
                continue
            out.append(RawEvidence(
                id=f"baidu:ref:{i}",
                fact=snippet,
                source=SourceType.WEB,
                origin=ref.get("url", "") or ref.get("web_anchor", ""),
                confidence=0.55,
            ))

        return out
