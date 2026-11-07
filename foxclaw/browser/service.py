"""
BrowserService —— HIR-Browser 在 Foxclaw 内的 async 安全接入层。

它解决把"同步 Playwright 浏览器"塞进"async FastAPI"的三个硬约束：

  A1 可选依赖：playwright（及 chromium 内核）是可选 extra。未安装时
     available()=False，perceive/act 返回结构化"不可用"而非崩溃，
     与 search 子系统的优雅降级一致。

  A2 线程亲和 + 事件循环禁区：Playwright sync API 不能在 asyncio 事件
     循环线程里运行，且其对象绑定创建它的线程。故本 Service 用一条
     max_workers=1 的**专用工作线程**独占持有会话——所有浏览器操作都
     被 run_in_executor 派发到这同一条线程，既满足线程亲和，也天然把
     单会话（v1 只支持单会话）的访问串行化。

  A3 感知入口：额外暴露 perceive()（只感知、不动作），供 agent/前端在
     决策前读取页面语义结构；原 WebAdapter 只有 execute。

对外方法都是 async，直接 await 即可；内部 _*_sync 方法只在工作线程上跑。
"""

import asyncio
import importlib.util
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional


class BrowserService:

    def __init__(self, headless: bool = True):
        self._headless = headless
        self._session = None
        self._executor: Optional[ThreadPoolExecutor] = None
        self._closed = False
        self._launch_error: Optional[str] = None
        # 保护 executor/session 的创建与关闭（仅协调，不进浏览器调用）
        self._guard = threading.Lock()

    # ── 能力探测 ────────────────────────────────────────────────
    @staticmethod
    def available() -> bool:
        """playwright 包是否可导入。注意：这不保证 chromium 内核已 install，
        内核缺失会在首次 launch 时暴露，由 _launch_error 记录并透出。"""
        return importlib.util.find_spec("playwright") is not None

    def status(self) -> dict:
        return {
            "available": self.available(),
            "headless": self._headless,
            "session_active": self._session is not None,
            "closed": self._closed,
            "launch_error": self._launch_error,
        }

    # ── 工作线程上运行的同步实现 ──────────────────────────────────
    def _get_executor(self) -> ThreadPoolExecutor:
        with self._guard:
            if self._closed:
                raise RuntimeError("BrowserService 已关闭")
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="foxclaw-browser")
            return self._executor

    def _ensure_session_sync(self):
        """在工作线程上惰性构造会话（首次 launch chromium）。"""
        if self._session is None:
            from .session.session_manager import SessionManager
            self._session = SessionManager(headless=self._headless)
        return self._session

    def _perceive_sync(self) -> dict:
        return self._ensure_session_sync().snapshot().to_dict()

    def _act_sync(self, action_dict: dict) -> dict:
        from .action.action_types import WebAction
        session = self._ensure_session_sync()
        return session.execute(WebAction.from_dict(action_dict))

    def _close_sync(self):
        if self._session is not None:
            try:
                self._session.close()
            finally:
                self._session = None

    async def _run(self, fn, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._get_executor(), fn, *args)

    # ── 对外 async API ─────────────────────────────────────────
    async def perceive(self) -> dict:
        """只感知当前页面语义结构，不改变页面状态。"""
        if not self.available():
            return self._unavailable()
        try:
            return {"ok": True, "semantic_state": await self._run(self._perceive_sync)}
        except Exception as e:
            return self._handle_error(e)

    async def act(self, action: dict) -> dict:
        """执行一个 Web 动作（navigate/click/type/scroll/extract/wait），返回校验后最新语义结构。"""
        if not self.available():
            return self._unavailable()
        if not isinstance(action, dict) or "type" not in action:
            return {"ok": False, "error": "非法动作：需 {type, target?, value?}"}
        try:
            return {"ok": True, **await self._run(self._act_sync, action)}
        except ValueError as e:  # 不支持的动作类型 / 非法编号
            return {"ok": False, "error": str(e)}
        except KeyError as e:    # 元素编号过期
            return {"ok": False, "error": str(e).strip('"')}
        except Exception as e:
            return self._handle_error(e)

    async def close(self):
        """释放浏览器会话并关停专用工作线程（幂等）。挂到 Foxclaw teardown。"""
        with self._guard:
            if self._closed:
                return
            self._closed = True
            executor = self._executor
        if executor is not None:
            try:
                # 会话的 close 必须回到工作线程执行（Playwright 对象线程亲和）
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(executor, self._close_sync)
            finally:
                executor.shutdown(wait=True)

    # ── 错误/降级处理 ──────────────────────────────────────────
    def _unavailable(self) -> dict:
        return {
            "ok": False,
            "available": False,
            "error": "浏览器子系统不可用：未安装 playwright（可选依赖）。"
                     "安装：pip install 'foxclaw[browser]' 且 playwright install chromium",
        }

    def _handle_error(self, e: Exception) -> dict:
        msg = str(e)
        # chromium 内核未安装是最常见的 launch 失败，给出可操作提示
        if "playwright install" in msg or "Executable doesn't exist" in msg:
            self._launch_error = msg
            return {"ok": False, "available": False,
                    "error": "playwright 已装但缺 chromium 内核，请运行：playwright install chromium"}
        return {"ok": False, "error": f"{type(e).__name__}: {msg}"}
