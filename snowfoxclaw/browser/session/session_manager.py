"""
SessionManager —— 单浏览器会话的生命周期管理与组件组装。

v1.0 明确只支持单会话（对应审计里"v1不做分布式/多浏览器集群"的边界）。
多会话池、并发调度属于 Infrastructure Plane，v2 再做。

【融合注记 A2】本类构造即 launch chromium（有线程亲和性），
必须始终在 BrowserService 的同一条专用工作线程上构造与调用。
"""

from ..driver.playwright_driver import PlaywrightDriver
from ..perception.dom_extractor import DomExtractor
from ..perception.element_indexer import ElementIndexer
from ..perception.semantic_reducer import SemanticReducer
from ..action.action_executor import ActionExecutor
from ..action.action_verifier import ActionVerifier
from ..action.action_types import WebAction


class SessionManager:

    def __init__(self, headless: bool = True):
        self.driver = PlaywrightDriver(headless=headless)
        self.indexer = ElementIndexer()
        self.dom_extractor = DomExtractor(self.driver)
        self.reducer = SemanticReducer(self.dom_extractor, self.indexer)
        self.executor = ActionExecutor(self.driver, self.indexer)
        self.verifier = ActionVerifier()

    def snapshot(self):
        """对外暴露的唯一"感知"入口"""
        return self.reducer.reduce()

    def execute(self, action: WebAction) -> dict:
        """
        对外暴露的唯一"执行"入口：
        执行前拍快照 -> 执行 -> 执行后拍快照 -> 校验 -> 返回最新语义结构

        注：执行前的 before 快照会重建元素编号表——这既服务于 diff 校验，
        也顺带刷新了可能已 detach 的旧句柄（页面在两次调用间可能已变化）。
        因此上层引用的编号应来自"上一次 execute/perceive 返回的 semantic_state"，
        且假定两次调用之间页面结构稳定（编号按 DOM 顺序稳定复现）。
        """
        before = self.snapshot()
        raw_result = self.executor.run(action)
        after = self.snapshot()

        verification = self.verifier.verify(before, after, action)

        return {
            "raw": raw_result,
            "semantic_state": after.to_dict(),
            "verified": verification,
        }

    def close(self):
        self.driver.close()
