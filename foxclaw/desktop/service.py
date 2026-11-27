"""DesktopService —— 桌面操控的 async 感知/操作接入层。

与 BrowserService 同构：可选依赖优雅降级，同步后端派发到工作线程，
感知统一走 observe（SC-03），操作统一走 check_action（SC-08）。
操作动作名约定为 `category:sub`，如 `mouse:click` / `shell:run` / `file:write`。
"""
import asyncio
import importlib.util
import sys

from .capture import capture_screen
from .windows import list_windows, window_action_sync
from .input import mouse_sync, keyboard_sync
from .clipboard import clipboard_read_sync, clipboard_write_sync
from .shell import shell_run_sync
from .file_ops import file_read_sync, file_write_sync, file_delete_sync
from .uia import ui_tree_sync, ui_action_sync, ui_batch_sync, uia_available
from .ocr import ocr_sync, ocr_available
from . import scope as _scope


class DesktopService:
    def __init__(self):
        self._closed = False

    @staticmethod
    def available() -> bool:
        """截图后端 mss 是否可导入。"""
        return importlib.util.find_spec("mss") is not None

    @staticmethod
    def windows_supported() -> bool:
        """键鼠/窗口/剪贴板等原生控制是否可用（Windows ctypes）。"""
        return sys.platform == "win32"

    def status(self) -> dict:
        return {
            "available": self.available(),
            "windows_supported": self.windows_supported(),
            "uia": uia_available(),
            "ocr": ocr_available(),
            "closed": self._closed,
        }

    # ── 感知层（走 observe 边界）──
    async def capture(self) -> dict:
        if not self.available():
            return self._unavailable("屏幕截图")
        return await self._run(capture_screen)

    async def list_windows(self) -> dict:
        if not self.windows_supported():
            return {"ok": False, "available": False,
                    "error": "窗口列举暂仅支持 Windows（macOS/Linux 预留）"}
        return await self._run(list_windows)

    async def ui_tree(self, target: str = "") -> dict:
        """UI Automation 感知：枚举前台窗口可交互元素（source=uia，走 observe 边界）。"""
        if not uia_available():
            return {"ok": False, "available": False,
                    "error": "UI Automation 暂仅支持 Windows（macOS/Linux 预留）"}
        return await self._run(ui_tree_sync, target)

    async def ocr(self, lang: str = "") -> dict:
        """屏幕 OCR 感知：整屏文字 + 逐词坐标（source=ocr，走 observe 边界）。"""
        if not self.available():
            return self._unavailable("屏幕 OCR")
        if not ocr_available():
            return {"ok": False, "available": False,
                    "error": "OCR 暂仅支持 Windows（macOS/Linux 预留）"}
        return await self._run(ocr_sync, lang)

    # ── 操作层（走 check_action 边界）──
    async def act(self, action: str, args: dict) -> dict:
        args = args or {}
        cat, _, sub = action.partition(":")
        handler = {
            "mouse": self._act_mouse,
            "keyboard": self._act_keyboard,
            "window": self._act_window,
            "clipboard": self._act_clipboard,
            "shell": self._act_shell,
            "file": self._act_file,
            "ui": self._act_ui,
        }.get(cat)
        if handler is None:
            return {"ok": False, "error": f"未知桌面操作: {action}"}
        return await handler(sub, args)

    async def _act_mouse(self, sub, a):
        return await self._run(mouse_sync, sub or "click", a.get("x", 0), a.get("y", 0))

    async def _act_keyboard(self, sub, a):
        return await self._run(keyboard_sync, sub or "type", a.get("text", ""), a.get("keys"))

    async def _act_window(self, sub, a):
        return await self._run(window_action_sync, sub or "focus", a.get("target", ""),
                               a.get("x", 0), a.get("y", 0),
                               a.get("width", 0), a.get("height", 0))

    async def _act_clipboard(self, sub, a):
        if sub == "write":
            return await self._run(clipboard_write_sync, a.get("text", ""))
        return await self._run(clipboard_read_sync)

    async def _act_shell(self, sub, a):
        # 执行层范围闸(与授权层独立):危险命令/敏感路径/工作目录外 → 不执行
        ok, why, cwd = _scope.check_shell(a.get("command", ""), a.get("cwd", ""))
        if not ok:
            return {"ok": False, "scope_denied": True, "error": f"shell 被范围限制拒绝:{why}"}
        r = await self._run(shell_run_sync, a.get("command", ""), cwd,
                            _scope.clamp_timeout(a.get("timeout", 30)))
        if isinstance(r, dict):
            for k in ("stdout", "stderr"):
                if k in r:
                    r[k], cut = _scope.truncate(r[k])
                    if cut:
                        r[f"{k}_truncated"] = True
        return r

    async def _act_file(self, sub, a):
        ok, why, path = _scope.check_path(a.get("path", ""))
        if not ok:
            return {"ok": False, "scope_denied": True, "error": f"文件操作被范围限制拒绝:{why}"}
        if sub == "write":
            return await self._run(file_write_sync, path, a.get("content", ""),
                                   bool(a.get("append", False)))
        if sub == "delete":
            return await self._run(file_delete_sync, path)
        if sub == "read":
            return await self._run(file_read_sync, path)
        # 此前未知子动作(file:move 等)静默落到 read,这里明确拒绝
        return {"ok": False, "error": f"未知文件操作: file:{sub}"}

    async def _act_ui(self, sub, a):
        locator = a.get("locator")
        if sub == "batch":
            return await self._run(ui_batch_sync, a.get("ops"))
        if sub == "click":
            loc = await self._run(ui_action_sync, "locate", locator)
            if not loc.get("ok"):
                return loc
            cx, cy = loc.get("center", (0, 0))
            return await self._run(mouse_sync, "click", cx, cy)
        return await self._run(ui_action_sync, sub, locator, a.get("value", ""))

    async def _run(self, fn, *args) -> dict:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, fn, *args)
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    @staticmethod
    def _unavailable(what: str) -> dict:
        return {"ok": False, "available": False,
                "error": f"{what}不可用：未安装可选依赖 mss/Pillow。"
                         "安装：pip install 'foxclaw[desktop]'"}
