"""桌面操控子系统端点（感知层 + 操作层）。"""
from pydantic import BaseModel

import foxclaw.api as _api
from .. import app


class DesktopActPayload(BaseModel):
    action: str
    args: dict = {}
    confirmed: bool = False
    # 写动作确认:首次调用返回 confirm_nonce,确认后带 confirmed=true + 该 nonce 重发
    # (同动作、同参数、有效期内、只能用一次)。只带 confirmed=true 不带 nonce 一律按未确认处理。
    confirm_nonce: str = ""


@app.get("/api/desktop/status")
async def desktop_status():
    """桌面操控子系统能力状态（available/windows_supported）。"""
    return _api.os_instance().desktop_service.status()


@app.get("/api/desktop/screen")
async def desktop_screen():
    """全屏截图（感知通道）。SC-03：经统一观察边界，未授权 fail-closed。"""
    r = await _api.os_instance().screen_capture(
        source="screen", scope="full", sensitivity="sensitive")
    return _perceive_resp(r)


@app.get("/api/desktop/windows")
async def desktop_windows():
    """窗口列举（感知通道）。SC-03：经统一观察边界，未授权 fail-closed。"""
    r = await _api.os_instance().window_list(
        source="windows", scope="full", sensitivity="private")
    return _perceive_resp(r)


@app.get("/api/desktop/uitree")
async def desktop_uitree():
    """UI Automation 元素树（感知通道）。SC-03：经统一观察边界，未授权 fail-closed。"""
    r = await _api.os_instance().ui_tree(
        source="uia", scope="full", sensitivity="sensitive")
    return _perceive_resp(r)


@app.get("/api/desktop/ocr")
async def desktop_ocr():
    """屏幕 OCR（感知通道）。SC-03：经统一观察边界，未授权 fail-closed。"""
    r = await _api.os_instance().ocr(
        source="ocr", scope="full", sensitivity="sensitive")
    return _perceive_resp(r)


@app.post("/api/desktop/act")
async def desktop_act(p: DesktopActPayload):
    """执行一个桌面操作（键鼠/窗口/剪贴板/shell/文件）。

    action 形如 `mouse:click` / `shell:run` / `file:write`。SC-08：先经操作意图授权边界
    （os.desktop_act → check_action），deny/confirm fail-closed；write/副作用动作需前端带
    confirmed=true **和**首次调用返回的 confirm_nonce 才放行(nonce 一次性、绑参数、5 分钟过期)。
    shell:run / file:delete 默认 deny(FOXCLAW_DESKTOP_ACT_DENY 可覆盖)。
    """
    return await _api.os_instance().desktop_act(p.action, p.args, confirmed=p.confirmed,
                                                confirm_nonce=p.confirm_nonce)


def _perceive_resp(r) -> dict:
    if not r.allowed:
        return {"ok": False, "denied": True, "reason": r.reason,
                "decision_id": r.decision_id}
    raw = r.raw if isinstance(r.raw, dict) else {"ok": True, "data": r.raw}
    resp = dict(raw)
    resp.update({"wrapped": r.wrapped, "source": r.source, "taint": r.taint,
                 "decision_id": r.decision_id})
    return resp
