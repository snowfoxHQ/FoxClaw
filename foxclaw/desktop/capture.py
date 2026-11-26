"""屏幕截图后端：mss 跨平台采集 + Pillow 编码 PNG。"""
import base64
from io import BytesIO


def capture_screen() -> dict:
    """全屏截图，返回 base64 PNG。由 DesktopService 派发到工作线程执行。"""
    from mss import mss
    from PIL import Image

    with mss() as sct:
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        shot = sct.grab(monitor)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return {
            "ok": True,
            "image": {
                "width": img.width,
                "height": img.height,
                "format": "png",
                "data": base64.b64encode(buf.getvalue()).decode("ascii"),
            },
        }
