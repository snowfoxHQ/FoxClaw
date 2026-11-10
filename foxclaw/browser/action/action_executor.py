"""
ActionExecutor —— 把 WebAction 翻译成真实浏览器操作。

只做翻译和调用，不做判断（判断动作是否该执行、是否合理，
不属于这一层，也不属于 HIR-Browser 整体，那是 Foxclaw/HAR 的职责）。
"""

from .action_types import WebAction


class ActionExecutor:

    def __init__(self, driver, element_indexer):
        self.driver = driver
        self.indexer = element_indexer

    def run(self, action: WebAction):
        handler = getattr(self, f"_do_{action.type}", None)
        if handler is None:
            raise NotImplementedError(f"未实现的动作类型: {action.type}")
        return handler(action)

    def _do_navigate(self, action: WebAction):
        from .url_policy import validate_navigate_url
        target = validate_navigate_url(action.target or "")
        self.driver.navigate(target)
        return {"navigated_to": target}

    def _do_click(self, action: WebAction):
        handle = self.indexer.resolve(action.target)
        self.driver.click(handle)
        return {"clicked": action.target}

    def _do_type(self, action: WebAction):
        handle = self.indexer.resolve(action.target)
        self.driver.type_text(handle, action.value or "")
        return {"typed": action.value, "into": action.target}

    def _do_scroll(self, action: WebAction):
        direction = action.value or "down"
        self.driver.scroll(direction)
        return {"scrolled": direction}

    def _do_extract(self, action: WebAction):
        return {"extract_requested": True}

    def _do_wait(self, action: WebAction):
        seconds = float(action.value or 1.0)
        self.driver.wait(seconds)
        return {"waited": seconds}
