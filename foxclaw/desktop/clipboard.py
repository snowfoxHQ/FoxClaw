"""剪贴板读写后端：Windows user32 剪贴板 API（零依赖）；其它平台降级。"""
import sys

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002
_GMEM_ZEROINIT = 0x0040


def _unsupported() -> dict:
    return {"ok": False, "available": False,
            "error": "剪贴板读写暂仅支持 Windows（macOS/Linux 预留）"}


def _set_signatures(user32, kernel32):
    """显式声明 argtypes/restype，避免 64 位句柄被默认 c_int 截断。"""
    import ctypes
    from ctypes import wintypes
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL


def clipboard_read_sync() -> dict:
    if sys.platform != "win32":
        return _unsupported()
    import ctypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    _set_signatures(user32, kernel32)
    if not user32.OpenClipboard(None):
        return {"ok": False, "error": "无法打开剪贴板"}
    try:
        h = user32.GetClipboardData(_CF_UNICODETEXT)
        if not h:
            return {"ok": True, "text": ""}
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            return {"ok": False, "error": "GlobalLock 失败"}
        try:
            text = ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(h)
        return {"ok": True, "text": text}
    finally:
        user32.CloseClipboard()


def clipboard_write_sync(text: str) -> dict:
    if sys.platform != "win32":
        return _unsupported()
    import ctypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    _set_signatures(user32, kernel32)
    if not user32.OpenClipboard(None):
        return {"ok": False, "error": "无法打开剪贴板"}
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        h = kernel32.GlobalAlloc(_GMEM_MOVEABLE | _GMEM_ZEROINIT, len(data))
        if not h:
            return {"ok": False, "error": "GlobalAlloc 失败"}
        ptr = kernel32.GlobalLock(h)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(_CF_UNICODETEXT, h):
            return {"ok": False, "error": "SetClipboardData 失败"}
        return {"ok": True, "length": len(text)}
    finally:
        user32.CloseClipboard()
