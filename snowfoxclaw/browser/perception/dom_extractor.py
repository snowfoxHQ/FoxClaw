"""
DOM 提取器。

职责边界很窄：只负责"从底层 driver 拿原始数据"，
不做任何压缩/裁剪——压缩是 SemanticReducer 的事。
这样分层是为了让"取数方式"和"压缩策略"可以独立演化
（比如以后换成 CDP driver，只需要改这一层）。
"""

# 只关心这些角色的元素，减少噪音；按需扩展
INTERACTIVE_ROLES = ("button", "link", "textbox", "checkbox", "radio", "combobox", "menuitem")


class DomExtractor:

    def __init__(self, driver):
        self.driver = driver

    def extract_interactive_elements(self):
        """
        返回底层元素句柄列表（具体类型由 driver 决定，Playwright 下是 ElementHandle）。
        只取可见 + 可交互的元素，不可见的直接丢弃，避免 LLM 尝试点击看不见的东西。
        """
        return self.driver.query_interactive_elements(INTERACTIVE_ROLES)

    def describe_element(self, handle) -> dict:
        """把一个元素句柄转成 role/text/attrs 的原始描述，交给 indexer 二次加工"""
        return self.driver.describe_element(handle)

    def extract_visible_text(self, max_chars: int = 2000) -> str:
        """
        提取页面主要可见文本，做长度上限控制。
        这不是给 LLM 做阅读理解用的全文，只是辅助上下文，
        真正结构化的信息应该来自 elements 列表。
        """
        text = self.driver.get_visible_text()
        return text[:max_chars]

    def current_url(self) -> str:
        return self.driver.current_url()

    def current_title(self) -> str:
        return self.driver.current_title()
