"""
PlaywrightDriver —— 默认底层驱动实现。

用 Playwright 的 sync API，因为 HIR v1.0 Kernel 的执行流程本身是
顺序同步的（详见 HIR ExecutionVM 设计：v1 不做并发调度）。
如果以后要支持并发多会话，再切换到 async API 或换 CDP driver。

注意：这里刻意用 role 相关的 CSS/属性选择器组合来找"可交互元素"，
没有依赖 accessibility tree API（虽然更准确，但部分 headless 场景下
获取成本更高），这是 v1 的工程妥协，标注在这里方便后续替换。

【融合适配 A1/A2】
- playwright 在 Foxclaw 里是**可选依赖**（extra `browser`），所以 import
  刻意放进 __init__ 而非模块顶层：仅"真正构造 driver"时才需要 playwright，
  `import foxclaw.browser` 本身不拖 playwright，缺失时子系统优雅降级而非崩溃。
- Playwright sync API **不能在 asyncio 事件循环线程里跑**，且其对象有线程亲和性；
  故本 driver 必须由 BrowserService 的专用工作线程独占构造与调用（见 service.py）。
"""

import time
from typing import List, Any

from .base_driver import BaseDriver


# role -> 对应的 CSS selector，v1 用简单选择器覆盖大多数场景
ROLE_SELECTOR_MAP = {
    "button": "button, [role=button], input[type=button], input[type=submit]",
    "link": "a[href]",
    "textbox": "input[type=text], input[type=search], input[type=email], "
               "input[type=password], input:not([type]), textarea",
    "checkbox": "input[type=checkbox]",
    "radio": "input[type=radio]",
    "combobox": "select",
    "menuitem": "[role=menuitem]",
}


class PlaywrightDriver(BaseDriver):

    def __init__(self, headless: bool = True):
        # A1：惰性导入——缺 playwright 时这里才抛 ImportError，由上层能力探测拦截，
        # 不会污染 `import foxclaw.browser`。
        from playwright.sync_api import sync_playwright

        self._pw = None
        self._browser = None
        self._page = None
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=headless)
            self._page = self._browser.new_page()
        except Exception:
            # A4：构造中途失败（如 chromium 内核未 install）也不能泄漏原生资源
            self._safe_teardown()
            raise

    def _safe_teardown(self):
        """尽力释放已创建的资源，任一步失败都不影响其余步骤。"""
        for closer in (
            lambda: self._browser.close() if self._browser else None,
            lambda: self._pw.stop() if self._pw else None,
        ):
            try:
                closer()
            except Exception:
                pass
        self._page = None
        self._browser = None
        self._pw = None

    def navigate(self, url: str) -> None:
        self._page.goto(url, wait_until="domcontentloaded")

    def query_interactive_elements(self, roles: tuple) -> List[Any]:
        selectors = [ROLE_SELECTOR_MAP[r] for r in roles if r in ROLE_SELECTOR_MAP]
        combined = ", ".join(selectors)
        elements = self._page.query_selector_all(combined)
        # 只保留当前可见的元素，避免 LLM 尝试操作看不见的东西
        return [el for el in elements if el.is_visible()]

    def describe_element(self, handle) -> dict:
        role = handle.evaluate(
            "el => el.getAttribute('role') || el.tagName.toLowerCase()"
        )
        text = handle.evaluate(
            "el => (el.innerText || el.value || el.placeholder || '').trim()"
        )
        attrs = handle.evaluate(
            """el => {
                const out = {};
                for (const attr of ['href', 'name', 'type', 'aria-label']) {
                    if (el.getAttribute(attr)) out[attr] = el.getAttribute(attr);
                }
                return out;
            }"""
        )
        return {"role": role, "text": text, "attrs": attrs}

    def click(self, handle) -> None:
        handle.click()

    def type_text(self, handle, text: str) -> None:
        handle.fill(text)

    def scroll(self, direction: str) -> None:
        delta = 600 if direction == "down" else -600
        self._page.mouse.wheel(0, delta)

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    def get_visible_text(self) -> str:
        return self._page.inner_text("body")

    def current_url(self) -> str:
        return self._page.url

    def current_title(self) -> str:
        return self._page.title()

    def close(self) -> None:
        self._safe_teardown()
