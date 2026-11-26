"""键鼠控制后端：Windows user32（零依赖）；其它平台降级。

鼠标用 SetCursorPos + mouse_event，键盘用 keybd_event(KEYEVENTF_UNICODE)。
全部同步函数，由 DesktopService 派发到工作线程执行。
"""
import sys

_VK_BY_NAME = {
    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "backspace": 0x08,
    "delete": 0x2E, "space": 0x20, "left": 0x25, "up": 0x26,
    "right": 0x27, "down": 0x28, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
}


def _unsupported(what: str) -> dict:
    return {"ok": False, "available": False,
            "error": f"{what}暂仅支持 Windows（macOS/Linux 预留）"}


def mouse_sync(action: str, x: int = 0, y: int = 0) -> dict:
    """move / click / right_click / double_click / drag / scroll。"""
    if sys.platform != "win32":
        return _unsupported("键鼠控制")
    import ctypes
    user32 = ctypes.windll.user32
    ML = 0x0002; MU = 0x0004; MR = 0x0008; MRU = 0x0010; MW = 0x0800
    x, y = int(x), int(y)
    if action == "move":
        user32.SetCursorPos(x, y)
    elif action == "click":
        user32.SetCursorPos(x, y)
        user32.mouse_event(ML, 0, 0, 0, 0); user32.mouse_event(MU, 0, 0, 0, 0)
    elif action == "right_click":
        user32.SetCursorPos(x, y)
        user32.mouse_event(MR, 0, 0, 0, 0); user32.mouse_event(MRU, 0, 0, 0, 0)
    elif action == "double_click":
        user32.SetCursorPos(x, y)
        for _ in range(2):
            user32.mouse_event(ML, 0, 0, 0, 0); user32.mouse_event(MU, 0, 0, 0, 0)
    elif action == "drag":
        user32.SetCursorPos(x, y)
        user32.mouse_event(ML, 0, 0, 0, 0); user32.mouse_event(MU, 0, 0, 0, 0)
    elif action == "scroll":
        user32.mouse_event(MW, 0, 0, y, 0)  # y 为滚动增量(±120 一格)
    else:
        return {"ok": False, "error": f"未知鼠标动作: {action}"}
    return {"ok": True, "action": action, "x": x, "y": y}


def _press_vk(user32, vk: int):
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 0x0002, 0)  # KEYEVENTF_KEYUP


def keyboard_sync(action: str, text: str = "", keys=None) -> dict:
    """type(文本) / press(单键列表) / hotkey(组合键，末尾为主键)。"""
    if sys.platform != "win32":
        return _unsupported("键鼠控制")
    import ctypes
    user32 = ctypes.windll.user32
    KU = 0x0002; UNI = 0x0004
    if action == "type":
        for ch in text:
            vk = ord(ch)
            user32.keybd_event(vk, 0, UNI, 0)
            user32.keybd_event(vk, 0, UNI | KU, 0)
    elif action == "press":
        for name in (keys or []):
            vk = _VK_BY_NAME.get(str(name).lower())
            if vk is None:
                return {"ok": False, "error": f"未知按键: {name}"}
            _press_vk(user32, vk)
    elif action == "hotkey":
        names = list(keys or []) if keys else text.split("+")
        vks = []
        for name in names:
            vk = _VK_BY_NAME.get(str(name).strip().lower())
            if vk is None:
                return {"ok": False, "error": f"未知按键: {name}"}
            vks.append(vk)
        if not vks:
            return {"ok": False, "error": "hotkey 需至少一个按键"}
        for vk in vks[:-1]:
            user32.keybd_event(vk, 0, 0, 0)  # 按下修饰键
        _press_vk(user32, vks[-1])
        for vk in reversed(vks[:-1]):
            user32.keybd_event(vk, 0, KU, 0)  # 释放修饰键
    else:
        return {"ok": False, "error": f"未知键盘动作: {action}"}
    return {"ok": True, "action": action}
