"""
Foxclaw API — FastAPI 后端
前端控制台通过这里获取所有数据
"""

import os
import asyncio
import queue
from pathlib import Path
from contextlib import asynccontextmanager

# 加载项目根目录的 .env(若存在),让所有 Key 从配置文件读取。
#
# 【FOXCLAW_SKIP_DOTENV】跳过 .env,只用进程环境变量 —— 专供测试/CI 使用。
# 为什么必须有这个逃生口:load_dotenv(override=False) 只保护「进程里已存在的键」。
# 测试若先 pop 掉某键(如 test_gateway.py pop FOXCLAW_API_KEY 以验证「未设即开放」),
# 再 import 本模块,.env 就会把它灌回来 → 测试实际跑在开发者的真实配置上。
# 后果有三:(1) 同一份代码在不同机器上结果不同,回归不可复现;
# (2) .env 里的真实 Key 会让测试真的打到外部 API(产生费用);
# (3) FOXCLAW_DATA_DIR=./foxclaw_data 会让测试写进用户的真实数据目录。
# 设 FOXCLAW_SKIP_DOTENV=1 后,进程环境变量成为唯一来源,测试环境完全自洽。
try:
    from ..core.envfile import load_env_file
    _env_path = Path(__file__).parent.parent.parent / ".env"
    if os.environ.get("FOXCLAW_SKIP_DOTENV", "").strip() in ("1", "true", "True", "yes"):
        print("[Foxclaw] ○ FOXCLAW_SKIP_DOTENV=1 —— 跳过 .env,仅用进程环境变量")
    elif _env_path.exists():
        # 非空值优先:系统环境变量里的同名旧 key 不再压住 .env(见 core/envfile.py)
        _shadows = load_env_file(_env_path)
        print("[Foxclaw] ✓ 已加载配置文件 .env")
        if _shadows:
            print("[Foxclaw] ! .env 覆盖了系统环境变量: " + ", ".join(_shadows))
except ImportError:
    pass  # 未装 python-dotenv 则用系统环境变量

from fastapi import FastAPI
from typing import Optional

from foxclaw import Foxclaw, __version__

# 中间件实现已移至 middleware.py;此处保留符号作 re-export(测试/外部经 foxclaw.api 访问),
# 冗余别名 `X as X` 让 ruff 识别为显式 re-export(F401 硬门)。install_middleware 在下方调用。
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

# 事件脊柱辅助(WS 净化 / HKC 预热)已移至 spine.py;`_register_spine_projection` 因
# `_WS_MUTED` 源码级断言约束仍留本文件(见 test_governance_audit)。
from .spine import _ws_safe_payload, _prewarm_hkc
# 共享 OS 实例的状态与访问器单独成模块:本文件已贴着 15KB 体积门上限(tools/size_check.py),
# 再往里塞代码会顶穿。`as os_instance` 是刻意的 —— 路由层写 `_api.os_instance()`,属再导出。
from ._state import current_os, set_os_instance
from ._state import os_instance as os_instance

# app 挂载/配置(CORS / HKC 星球数据面 / 前端静态)已移至 bootstrap.py,在下方按原顺序调用。
# 注意:该文件绝不能叫 app.py —— 包内已有变量 `app = FastAPI(...)`,同名子模块会让
# `from .. import app` 在类型检查器(以及运行期早于赋值的导入)里解析成模块而非实例。
from .bootstrap import _configure_cors, _mount_hkc, _mount_frontend

# 线程安全事件队列:harness 在任意线程触发的事件先入队,
# 由后台 async 任务统一广播到 WebSocket(解决跨线程 create_task 失败问题)
_event_queue: "queue.Queue" = queue.Queue()
_broadcast_task: Optional[asyncio.Task] = None
# HKC 后台预热守护线程:关闭 lifespan 前必须 join,否则 aclose() 关闭 SQLite 连接时
# 预热线程仍在 build_index() → use-after-free → Windows access violation(偶发崩溃)。
_prewarm_thread = None


def _register_spine_projection(os_instance: Foxclaw):
    """【P0·统一事件脊柱】注册**单一投影**:EventSpine 上的每一条事件 → 线程安全队列
    → WS 广播。取代旧的"逐 Agent 逐事件手工转发"(那时只有 harness 4 类事件能到前端,
    truth/process/memory 等系统事件被漏掉)。现在 harness 事件与系统事件同经脊柱,
    一个投影全量观测。payload 经 _ws_safe_payload 净化(JSON 安全 + 有界),避免脏/大
    对象丢连接或泛洪。event/data 形状与旧版一致(前端不受影响,未知事件被忽略)。"""
    # 高频事件:给重放/审计用(仍在脊柱 history + robot sink),不广播到 WS 以免泛洪。
    #   · agent.cognition.*：逐 stage 认知事件
    #   · knowledge.created/updated：HKC 批量摄入一本书→数百条,量大;merged/conflict/
    #     ability/agent.used_ability 等"有意义"的知识事件仍广播(低频、可观测知识演化)。
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
    # 启动自检:未设 FOXCLAW_API_KEY 即等于无鉴权。此处只做提醒(绑定地址由启动方式决定,
    # 拿不到 host);__main__ 入口对「非回环 + 无鉴权」有硬拦截。
    if not os.environ.get("FOXCLAW_API_KEY", "").strip():
        print("[Foxclaw API] ⚠ 未设置 FOXCLAW_API_KEY —— 当前无鉴权,"
              "仅本地回环安全;对外暴露前务必设置。")
    os_inst = Foxclaw(
        data_dir=os.environ.get("FOXCLAW_DATA_DIR", "./foxclaw_data"),
        shadow_rounds=settings.get_int("FOXCLAW_SHADOW_ROUNDS"),
    )
    # 状态存进 _state 模块,路由层经 os_instance() 取,不再跨模块直接读模块变量。
    set_os_instance(os_inst)
    _register_spine_projection(os_inst)
    # trace 落库(与其它 SQLite 同放 data_dir):内存环只留最近若干条且重启即失,
    # 落库后才能"查昨天那次慢请求"。只留慢的/出错的 + 采样,见 tracing._should_persist。
    try:
        from ..core.tracing import TRACES
        TRACES.attach_db(Path(os_inst.data_dir) / "traces.db")
    except Exception as e:
        print(f"[Foxclaw API] ⚠ trace 持久化未启用: {e}")
    # 后台任务:把队列里的事件广播到所有 WebSocket
    _broadcast_task = asyncio.create_task(_drain_event_queue())
    print("[Foxclaw API] ✓ 事件广播任务已启动")
    # 预热 HKC:开机后台加载知识核心(装配 + HKC 多语言 embedding 模型 + 搜索索引 over 全量 KU),
    # 免得"重启后第一次知识查询"触发冷装配卡 ~1 分钟。后台守护线程跑、不阻塞启动;失败静默。
    _prewarm_hkc(os_inst)
    if _foxclaw_mcp is not None:
        # 内置 MCP 服务器的 session_manager 需在 app lifespan 内 run()
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
    # 释放持有原生资源的子系统(浏览器专用线程/会话),避免进程无法干净退出。
    # 先 join HKC 预热线程:预热在后台线程对 SQLite 建索引,若不 join 就直接 aclose()
    # 关闭 SQLite 连接,会与预热线程产生 use-after-free → Windows access violation。
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
            # 带超时地阻塞取队列:超时让线程周期性醒来,
            # 以便被 cancel 时 executor 线程能干净退出(否则进程无法结束)
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


# ── 内置 MCP 服务器(默认关;FOXCLAW_MCP_SERVER=1 开)──────────
# 把只读知识+记忆暴露成 MCP 工具,挂 /api/mcp/(自动受 X-API-Key 中间件保护)。
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
    app.mount("/api/mcp", _foxclaw_mcp_app)   # 受 _api_key_guard 保护(startswith /api)

# ── CORS 收紧:实现已移至 app.py ───────────────────────────
_configure_cors(app)


# ── 中间件注册(鉴权/网关限流/追踪/指标):实现已移至 middleware.py,这里按原顺序集中挂载 ──
install_middleware(app)

# ── WebSocket 路由:实现已移至 routes/ws.py ────────────────
from .routes import ws as _ws
_ws.register(app)

# ── 系统/监控/追踪路由:实现已移至 routes/system.py(import 即注册) ────────────────
from .routes import system  # noqa: F401

# ── AHS 执行链路由:实现已移至 routes/ahs.py(import 即注册) ────────────────
from .routes import ahs  # noqa: F401

# ── 记忆/会话/偏好:routes/memory.py(import 即注册) ────────────────
from .routes import memory  # noqa: F401

# ── 任务执行/流式直答/流式任务:routes/task.py(import 即注册) ────────────────
from .routes import task  # noqa: F401

# ── 领域内核(财务/学习/行程):routes/domain.py(import 即注册) ────────────────
from .routes import domain  # noqa: F401

# ── 断点续跑现场:routes/recovery.py(import 即注册) ────────────────
from .routes import recovery  # noqa: F401

# ── 主管编排:routes/orchestration.py(import 即注册) ────────────────
from .routes import orchestration  # noqa: F401

# ── 浏览器子系统:routes/browser.py(import 即注册) ────────────────
from .routes import browser  # noqa: F401

# ── 知识核心(HKC):routes/knowledge.py(import 即注册) ────────────────
from .routes import knowledge  # noqa: F401

# ── SM-2 复习面:routes/review.py(import 即注册) ────────────────
from .routes import review  # noqa: F401

# ── 认知搜索(HSR):routes/search.py(import 即注册) ────────────────
from .routes import search  # noqa: F401

# ── 聊天附件上传/下载(多模态渲染·步骤7):routes/attachments.py(import 即注册) ────────────────
from .routes import attachments  # noqa: F401

# ── 前端 LLM 管理(下拉式选模型 + 自定义):routes/llm_admin.py(import 即注册) ──────
from .routes import llm_admin  # noqa: F401

# 搬移前这些是 api 模块级符号,测试/外部仍经 foxclaw.api 访问 → re-export
from .routes.task import _sanitize_images, _sanitize_attachments, _MAX_IMAGES, _MAX_IMG_B64, _STREAM_LIMITER  # noqa: F401
from .routes.domain import _parse_import_rows  # noqa: F401
from .routes.knowledge import knowledge_ability as knowledge_ability, _HKCInsuffCov as _HKCInsuffCov  # noqa: F401
from ..core import settings


# ── HKC 星球数据面:实现已移至 app.py ─────────────────────
_mount_hkc(app)


# ── 前端静态文件:实现已移至 app.py ─────────────────────────
_mount_frontend(app)


if __name__ == "__main__":
    import uvicorn

    # 默认只绑回环地址(本地开发)。对外暴露必须显式设 FOXCLAW_HOST,且应同时设置 FOXCLAW_API_KEY——
    # 否则该端口上任何可达者都能读写记忆、执行技能、摄入文件(这些端点全部无鉴权)。
    # 用 `python -m uvicorn foxclaw.api:app --host ...` 启动时会绕过本检查,故 lifespan 另有软提醒。
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
