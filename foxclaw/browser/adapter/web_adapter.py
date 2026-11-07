"""
WebAdapter —— HIR-Browser 对外的 HIR-Kernel 门面（match/execute 约定）。

HIR ExecutionVM 只认识这一个类。所有内部的
perception/action/driver/session 复杂度都被封装在这里，
不向 HIR Core 泄露任何实现细节。

注意：在 Foxclaw 内，async 路由请走 BrowserService（专用线程 + async 包装），
不要直接在事件循环线程里调用本类的 execute（Playwright sync API 会报错）。
本类保留是为了与独立 HIR-Kernel 场景兼容。
"""

from .base_adapter import BaseAdapter
from ..session.session_manager import SessionManager
from ..action.action_types import WebAction, SUPPORTED_ACTIONS


class WebAdapter(BaseAdapter):

    def __init__(self, headless: bool = True):
        self._session = None
        self._headless = headless

    def _ensure_session(self):
        if self._session is None:
            self._session = SessionManager(headless=self._headless)
        return self._session

    def match(self, action: dict) -> bool:
        return action.get("type") in SUPPORTED_ACTIONS

    def execute(self, action: dict) -> dict:
        session = self._ensure_session()
        web_action = WebAction.from_dict(action)
        result = session.execute(web_action)

        return {
            "type": "web_adapter_result",
            "action": action,
            "data": result,
        }

    def close(self):
        if self._session is not None:
            self._session.close()
            self._session = None
