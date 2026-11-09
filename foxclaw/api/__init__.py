"""
Foxclaw API — FastAPI 后端
前端控制台通过这里获取所有数据
"""

import threading
import os
import asyncio
import queue
from pathlib import Path
from contextlib import asynccontextmanager

try:
    from ..core.envfile import load_env_file
    _env_path = Path(__file__).parent.parent.parent / ".env"
    if os.environ.get("FOXCLAW_SKIP_DOTENV", "").strip() in ("1", "true", "True", "yes"):
        print("[Foxclaw] ○ FOXCLAW_SKIP_DOTENV=1 —— 跳过 .env,仅用进程环境变量")
    elif _env_path.exists():
        _shadows = load_env_file(_env_path)
        print("[Foxclaw] ✓ 已加载配置文件 .env")
        if _shadows:
            print("[Foxclaw] ! .env 覆盖了系统环境变量: " + ", ".join(_shadows))
except ImportError:
    pass

from fastapi import FastAPI
from typing import Optional

from foxclaw import Foxclaw, __version__

from .middleware import (
    install_middleware,
    _constant_time_eq as _constant_time_eq,
    _is_guarded_path as _is_guarded_path,
    _gateway_on as _gateway_on,
    _GATEWAY as _GATEWAY,
    _MAX_BODY as _MAX_BODY,
    _api_key_guard as _api_key_guard,
    _gateway_mw as _gateway_mw,
    _trace_mw as _trace_mw,
    _metrics_mw as _metrics_mw,
)

from .routes.ws import manager, harness_websocket as harness_websocket

from .spine import _ws_safe_payload, _prewarm_hkc
from ._state import current_os, set_os_instance
from ._state import os_instance as os_instance

from .bootstrap import _configure_cors, _mount_hkc, _mount_frontend

_event_queue: "queue.Queue" = queue.Queue()
_broadcast_task: Optional[asyncio.Task] = None
_prewarm_thread: "threading.Thread | None" = None


def _register_spine_projection(os_instance: Foxclaw):
    """【P0·统一事件脊柱】注册**单一投影**:EventSpine 上的每一条事件 → 线程安全队列
    → WS 广播。取代旧的"逐 Agent 逐事件手工转发"(那时只有 harness 4 类事件能到前端,
    truth/process/memory 等系统事件被漏掉)。现在 harness 事件与系统事件同经脊柱,
    一个投影全量观测。payload 经 _ws_safe_payload 净化(JSON 安全 + 有界),避免脏/大
    对象丢连接或泛洪。event/data 形状与旧版一致(前端不受影响,未知事件被忽略)。"""
    _WS_MUTED = ("knowledge.created", "knowledge.updated")

    def project(evt):
        if evt.topic.startswith("agent.cognition.") or evt.topic in _WS_MUTED:
            return
        try:
            _event_queue.put_nowait(
                {"event": evt.topic, "data": _ws_safe_payload(evt.payload)})
        except Exception:
            pass
    os_instance.event_spine.project(project)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _broadcast_task
    if not os.environ.get("FOXCLAW_API_KEY", "").strip():
        print("[Foxclaw API] ⚠ 未设置 FOXCLAW_API_KEY —— 当前无鉴权,"
              "仅本地回环安全;对外暴露前务必设置。")
    os_inst = Foxclaw(
        data_dir=os.environ.get("FOXCLAW_DATA_DIR", "./foxclaw_data"),
        shadow_rounds=settings.get_int("FOXCLAW_SHADOW_ROUNDS"),
    )
    set_os_instance(os_inst)
    _register_spine_projection(os_inst)
    try:
        from ..core.tracing import TRACES
        TRACES.attach_db(Path(os_inst.data_dir) / "traces.db")
    except Exception as e:
        print(f"[Foxclaw API] ⚠ trace 持久化未启用: {e}")
    _broadcast_task = asyncio.create_task(_drain_event_queue())
    print("[Foxclaw API] ✓ 事件广播任务已启动")
    _prewarm_hkc(os_inst)
    if _foxclaw_mcp is not None:
        async with _foxclaw_mcp.session_manager.run():
            print("[Foxclaw API] ✓ 内置 MCP 服务器已挂载 /api/mcp/")
            yield
    else:
        yield
    if _broadcast_task:
        _broadcast_task.cancel()
        try:
            await _broadcast_task
        except asyncio.CancelledError:
            pass
    if _prewarm_thread is not None and _prewarm_thread.is_alive():
        _prewarm_thread.join(timeout=120.0)
    live = current_os()
    if live is not None:
        await live.aclose()
    print("[Foxclaw API] 关闭中...")


async def _drain_event_queue():
    """后台 async 循环:从线程安全队列取事件,广播到 WebSocket。"""
    import queue as _queue_mod
    loop = asyncio.get_running_loop()
    while True:
        try:
            try:
                event = await loop.run_in_executor(
                    None, lambda: _event_queue.get(timeout=0.5))
            except _queue_mod.Empty:
                continue
            await manager.broadcast(event)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(0.1)


_foxclaw_mcp = None
_foxclaw_mcp_app = None
if os.environ.get("FOXCLAW_MCP_SERVER", "0").strip() in ("1", "true", "True"):
    from ..mcp_server import build_foxclaw_mcp
    _mcp_agents = os.environ.get("FOXCLAW_MCP_AGENTS", "0").strip() in ("1", "true", "True")
    _foxclaw_mcp = build_foxclaw_mcp(lambda: current_os(), expose_agents=_mcp_agents)
    _foxclaw_mcp_app = _foxclaw_mcp.streamable_http_app()
    if _mcp_agents:
        print("[Foxclaw API] ⚠ MCP 已暴露 Agent 调度(ask_foxclaw)——外部客户端可驱动你的认知 Agent")
    if not os.environ.get("FOXCLAW_API_KEY", "").strip():
        print("[Foxclaw API] ⚠ 内置 MCP 服务器已开启但未设 FOXCLAW_API_KEY —— "
              "对外暴露前务必设置,否则任何人可读你的知识/记忆")
    if os.environ.get("FOXCLAW_CORS_ORIGINS", "").strip() == "*":
        print("[Foxclaw API] ⚠ MCP 已开启但 CORS=* —— 生产建议用 FOXCLAW_CORS_ORIGINS 收紧允许来源")

app: FastAPI = FastAPI(
    title="Foxclaw API",
    version=__version__,
    description="Cognitive Agent Operating System",
    lifespan=lifespan,
)
if _foxclaw_mcp_app is not None:
    app.mount("/api/mcp", _foxclaw_mcp_app)

_configure_cors(app)


install_middleware(app)

from .routes import ws as _ws
_ws.register(app)

from .routes import system  # noqa: F401

from .routes import ahs  # noqa: F401

from .routes import memory  # noqa: F401

from .routes import task  # noqa: F401

from .routes import domain  # noqa: F401

from .routes import recovery  # noqa: F401

from .routes import orchestration  # noqa: F401

from .routes import browser  # noqa: F401

from .routes import knowledge  # noqa: F401

from .routes import review  # noqa: F401

from .routes import search  # noqa: F401

from .routes import attachments  # noqa: F401

from .routes import llm_admin  # noqa: F401

from .routes.task import _sanitize_images, _sanitize_attachments, _MAX_IMAGES, _MAX_IMG_B64, _STREAM_LIMITER  # noqa: F401
from .routes.domain import _parse_import_rows  # noqa: F401
from .routes.knowledge import knowledge_ability as knowledge_ability, _HKCInsuffCov as _HKCInsuffCov  # noqa: F401
from ..core import settings


_mount_hkc(app)


_mount_frontend(app)


if __name__ == "__main__":
    import uvicorn

    _host = os.environ.get("FOXCLAW_HOST", "127.0.0.1").strip() or "127.0.0.1"
    _port = settings.get_int("FOXCLAW_PORT")
    _LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
    if _host not in _LOOPBACK and not os.environ.get("FOXCLAW_API_KEY", "").strip():
        print(
            f"\n[Foxclaw API] ⚠⚠  绑定 {_host} 但未设置 FOXCLAW_API_KEY:\n"
            "    该端口上任何可达者都能读写记忆、执行技能、摄入文件。\n"
            "    请设置 FOXCLAW_API_KEY,或改用 FOXCLAW_HOST=127.0.0.1。\n"
            "    确认自担风险,可设 FOXCLAW_ALLOW_INSECURE=1 跳过此检查。\n"
        )
        if os.environ.get("FOXCLAW_ALLOW_INSECURE", "").strip() != "1":
            raise SystemExit("[Foxclaw API] 已拒绝启动:非回环绑定 + 无鉴权。")
    uvicorn.run(
        "foxclaw.api:app",
        host=_host,
        port=_port,
        reload=True,
    )
