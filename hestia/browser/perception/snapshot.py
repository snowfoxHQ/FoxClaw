"""
页面状态快照。

Snapshot 只存"语义化之后"的轻量结构，不存原始 DOM——
原始 DOM 只在 dom_extractor 里短暂存在，绝不向上层传递，
否则又会掉回"把整个 DOM 喂给 LLM"的老问题。
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any


@dataclass
class Snapshot:
    url: str
    title: str
    elements: List[Dict[str, Any]] = field(default_factory=list)
    text_summary: str = ""

    def element_ids(self) -> set:
        return {el["id"] for el in self.elements}

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "elements": self.elements,
            "text_summary": self.text_summary,
        }
