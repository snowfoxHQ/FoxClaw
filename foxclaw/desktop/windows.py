"""窗口列举 + 窗口控制后端：Windows ctypes（零依赖）；其它平台降级。"""
import sys


def _unsupported(what: str) -> dict:
    return {"ok": False, "available": False,
            "error": f"{what}暂仅支持 Windows（macOS/Linux 预留）"}


def _setup_signatures(user32):
    """显式声明 argtypes/restype，避免 64 位句柄被默认 c_int 截断。"""
    import ctypes
    from ctypes import wintypes
    user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = wintypes.INT
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, wintypes.INT]
    user32.GetWindowTextW.restype = wintypes.INT
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, wintypes.INT]
    user32.GetClassNameW.restype = wintypes.INT
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, wintypes.INT]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.INT, wintypes.INT,
                                    wintypes.INT, wintypes.INT, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL


def _enum_proc(ctypes, wintypes, cb):
    return ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(cb)


def list_windows() -> dict:
    if sys.platform != "win32":
        return _unsupported("窗口列举")
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    _setup_signatures(user32)
    wins = []

    def _cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        wins.append({"handle": int(hwnd), "title": title, "class": cls.value})
        return True

    user32.EnumWindows(_enum_proc(ctypes, wintypes, _cb), 0)
    return {"ok": True, "count": len(wins), "windows": wins}


def _find_window(user32, ctypes, wintypes, target: str) -> int:
    """按 handle(数字) 或标题(子串) 定位窗口；空 target = 前台窗口。"""
    if target == "":
        return int(user32.GetForegroundWindow())
    try:
        return int(target)
    except ValueError:
        pass
    found = []

    def _cb(hwnd, _lp):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if target.lower() in buf.value.lower():
                found.append(int(hwnd))
                return False  # 命中即停
        return True

    user32.EnumWindows(_enum_proc(ctypes, wintypes, _cb), 0)
    return found[0] if found else 0


def window_action_sync(action: str, target: str = "", x: int = 0, y: int = 0,
                       width: int = 0, height: int = 0) -> dict:
    """focus / close / minimize / maximize / restore / move / resize。"""
    if sys.platform != "win32":
        return _unsupported("窗口控制")
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    _setup_signatures(user32)
    hwnd = _find_window(user32, ctypes, wintypes, target)
    if not hwnd:
        return {"ok": False, "error": f"未找到窗口: {target or '(当前前台)'}"}
    if action == "focus":
        user32.SetForegroundWindow(hwnd)
    elif action == "close":
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    elif action == "minimize":
        user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
    elif action == "maximize":
        user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
    elif action == "restore":
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    elif action == "move":
        user32.SetWindowPos(hwnd, 0, int(x), int(y), 0, 0, 0x0001 | 0x0004)
    elif action == "resize":
        user32.SetWindowPos(hwnd, 0, 0, 0, int(width), int(height), 0x0002 | 0x0004)
    else:
        return {"ok": False, "error": f"未知窗口动作: {action}"}
    return {"ok": True, "action": action, "handle": hwnd}
