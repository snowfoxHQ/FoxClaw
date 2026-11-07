"""WebSocket:实时 Harness 事件流(连接管理 + 端点)。

从 foxclaw/api.py 原样搬移(纯搬移,零行为变更)。manager 由 api/__init__.py re-export
给后台广播任务 _drain_event_queue 使用。
"""

import os

from fastapi import WebSocket, WebSocketDisconnect

from ..middleware import _constant_time_eq, _loopback_bypass_on, _is_loopback_client


class ConnectionManager:
    def __init__(self):
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        self.connections.remove(ws)

    async def broadcast(self, data: dict):
        for ws in self.connections[:]:
            try:
                await ws.send_json(data)
            except Exception:
                self.connections.remove(ws)


manager = ConnectionManager()


async def harness_websocket(websocket: WebSocket):
    """
    实时 Harness 事件流。
    事件转发器在启动时已注册到线程安全队列,这里只负责接受连接并保活。

    鉴权:HTTP 中间件管不到 websocket scope,故 WS 的鉴权必须在端点内做。
    浏览器无法给 WS 握手设请求头,因此接受 ?key= 查询参数(也兼容能设头的非浏览器
    客户端的 X-API-Key 头)。未设 FOXCLAW_API_KEY 时开放;设了时本机(127.0.0.1/::1)
    客户端也免 ?key=(与 HTTP 中间件同口径的 loopback bypass)。
    """
    api_key = os.environ.get("FOXCLAW_API_KEY", "").strip()
    if api_key:
        # 【P3-loopback】与 HTTP 中间件同口径:本机(127.0.0.1 / ::1)WS 客户端免 ?key=
        # 设 FOXCLAW_LOOPBACK_BYPASS=0 可关。详见 foxclaw.api.middleware._loopback_bypass_on
        ws_host = websocket.client.host if websocket.client else ""
        if not (_loopback_bypass_on() and _is_loopback_client(ws_host)):
            provided = (websocket.query_params.get("key")
                        or websocket.headers.get("x-api-key"))
            # 【P3-1】常量时间比较(与 HTTP 中间件同一辅助函数);None 由 or "" 兜底——
            # api_key 非空时 not eq("", api_key) 必为 True,与原 None != api_key 行为一致
            if not _constant_time_eq(provided or "", api_key):
                await websocket.close(code=1008)   # policy violation
                return
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # 保持连接
    except WebSocketDisconnect:
        manager.disconnect(websocket)


def register(app):
    app.add_api_websocket_route("/ws/harness", harness_websocket)
