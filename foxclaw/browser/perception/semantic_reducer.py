"""
SemanticReducer —— HIR-Browser 的第一个核心壁垒。

问题：不能把整个 DOM 丢给 LLM（token 爆炸 + 噪音太多）。
方案：只保留"可交互元素 + 编号 + 简短文本"，用数字 ID 代替 selector/坐标，
     LLM 输出 click(3) 而不是猜 CSS path。

这一层不关心底层用什么 driver，只依赖 DomExtractor 和 ElementIndexer 的接口。
"""

from .snapshot import Snapshot


class SemanticReducer:

    def __init__(self, dom_extractor, element_indexer):
        self.dom_extractor = dom_extractor
        self.indexer = element_indexer

    def reduce(self) -> Snapshot:
        handles = self.dom_extractor.extract_interactive_elements()

        indexed_elements = self.indexer.assign(
            handles,
            describe_fn=self.dom_extractor.describe_element,
        )

        return Snapshot(
            url=self.dom_extractor.current_url(),
            title=self.dom_extractor.current_title(),
            elements=indexed_elements,
            text_summary=self.dom_extractor.extract_visible_text(),
        )
