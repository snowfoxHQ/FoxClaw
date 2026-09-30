"""
Hestia OS API — FastAPI 后端
前端控制台通过这里获取所有数据
"""

import os
import sys
import asyncio
import json
import queue
import threading
from pathlib import Path
from contextlib import asynccontextmanager

# 【跨平台启动修复】Windows 控制台默认 GBK(cp936),无法编码 ✓/⚠/· 等诊断字符 → 启动打印
# 抛 UnicodeEncodeError 直接崩溃(服务器起不来,用户只能双击 .html 文件)。把 stdout/stderr
# 重配置为 UTF-8(errors=replace 兜底),使所有平台/控制台的诊断打印都安全,不依赖 PYTHONUTF8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 加载项目根目录的 .env(若存在),让所有 Key 从配置文件读取。
#
# 【HESTIA_SKIP_DOTENV】跳过 .env,只用进程环境变量 —— 专供测试/CI 使用。
# 为什么必须有这个逃生口:load_dotenv(override=False) 只保护「进程里已存在的键」。
# 测试若先 pop 掉某键(如 test_gateway.py pop HESTIA_API_KEY 以验证「未设即开放」),
# 再 import 本模块,.env 就会把它灌回来 → 测试实际跑在开发者的真实配置上。
# 后果有三:(1) 同一份代码在不同机器上结果不同,回归不可复现;
# (2) .env 里的真实 Key 会让测试真的打到外部 API(产生费用);
# (3) HESTIA_DATA_DIR=./hestia_data 会让测试写进用户的真实数据目录。
# 设 HESTIA_SKIP_DOTENV=1 后,进程环境变量成为唯一来源,测试环境完全自洽。
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent.parent / ".env"
    if os.environ.get("HESTIA_SKIP_DOTENV", "").strip() in ("1", "true", "True", "yes"):
        print("[HestiaOS] ○ HESTIA_SKIP_DOTENV=1 —— 跳过 .env,仅用进程环境变量")
    elif _env_path.exists():
        load_dotenv(_env_path)
        print(f"[HestiaOS] ✓ 已加载配置文件 .env")
except ImportError:
    pass  # 未装 python-dotenv 则用系统环境变量

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, PlainTextResponse
from pydantic import BaseModel
from typing import Optional

from hestia import HestiaOS, __version__


# ── 全局 OS 实例 ───────────────────────────────────────────
_os_instance: Optional[HestiaOS] = None

# 线程安全事件队列:harness 在任意线程触发的事件先入队,
# 由后台 async 任务统一广播到 WebSocket(解决跨线程 create_task 失败问题)
_event_queue: "queue.Queue" = queue.Queue()
_broadcast_task: Optional[asyncio.Task] = None


def _ws_safe_payload(payload, _maxlen: int = 4000):
    """把任意事件 payload 收敛成 **JSON 安全且有界** 的形式,供 WS 广播。
    统一投影会把系统事件(execution.completed 等含原始 result/任意对象)也推给 WS,
    必须防两类真实故障:
      · 非 JSON 可序列化对象 → ws.send_json 抛异常 → broadcast 静默丢弃该连接;
      · 超大 payload(整段 LLM 回复 / 大 result,且随真值链被广播多次)→ 向所有 WS 泛洪。
    脊柱本身仍保留全保真事件(供 history / 审计 / 其它投影),净化只发生在 WS 出口。"""
    try:
        raw = json.dumps(payload, default=str, ensure_ascii=False)
    except Exception:
        return {"_repr": str(payload)[:_maxlen]}
    if len(raw) > _maxlen:
        return {"_truncated": True, "_preview": raw[:_maxlen]}
    return json.loads(raw)


def _register_spine_projection(os_instance: HestiaOS):
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


def _prewarm_hkc(osi):
    """后台预热 HKC:装配容器 + 建搜索索引(bm25+faiss over 全量 KU,顺带加载 HKC
    多语言 embedding 模型)+ 载入 learning。把"首次知识查询"的一次性冷成本(~几十秒到
    1 分钟)挪到开机后台完成,之后所有知识查询都不再冷。守护线程、失败静默,绝不影响主应用。
    索引重建与查询共用 HybridSearch 锁 → 预热期间若有查询会短暂等待,预热完即快。"""
    import threading, time as _t

    def _run():
        t0 = _t.time()
        try:
            svc = getattr(osi, "hkc", None)      # 访问即触发 HKC 惰性装配
            if not svc or not getattr(svc, "available", False):
                return
            c = svc.container
            try:
                c.search.build_index()           # 全量 KU 建索引 → 加载 embedding 模型(冷成本大头)
            except Exception as e:
                print(f"[HestiaOS API] · HKC 预热(search)跳过: {e}")
            try:
                lr = getattr(osi, "learning_runtime", None)
                if lr is not None:
                    lr._ensure_hkc_loaded()
            except Exception:
                pass
            print(f"[HestiaOS API] ✓ HKC 预热完成（{_t.time()-t0:.1f}s）: 首次知识查询不再冷装配")
        except Exception as e:
            print(f"[HestiaOS API] · HKC 预热未启用(可选): {e}")

    threading.Thread(target=_run, name="hkc-prewarm", daemon=True).start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _os_instance, _broadcast_task
    # 启动自检:未设 HESTIA_API_KEY 即等于无鉴权。此处只做提醒(绑定地址由启动方式决定,
    # 拿不到 host);__main__ 入口对「非回环 + 无鉴权」有硬拦截。
    if not os.environ.get("HESTIA_API_KEY", "").strip():
        print("[HestiaOS API] ⚠ 未设置 HESTIA_API_KEY —— 当前无鉴权,"
              "仅本地回环安全;对外暴露前务必设置。")
    _os_instance = HestiaOS(
        data_dir=os.environ.get("HESTIA_DATA_DIR", "./hestia_data"),
        shadow_rounds=int(os.environ.get("HESTIA_SHADOW_ROUNDS", "20")),
    )
    _register_spine_projection(_os_instance)
    # trace 落库(与其它 SQLite 同放 data_dir):内存环只留最近若干条且重启即失,
    # 落库后才能"查昨天那次慢请求"。只留慢的/出错的 + 采样,见 tracing._should_persist。
    try:
        from .core.tracing import TRACES
        TRACES.attach_db(Path(_os_instance.data_dir) / "traces.db")
    except Exception as e:
        print(f"[HestiaOS API] ⚠ trace 持久化未启用: {e}")
    # 后台任务:把队列里的事件广播到所有 WebSocket
    _broadcast_task = asyncio.create_task(_drain_event_queue())
    print("[HestiaOS API] ✓ 事件广播任务已启动")
    # 预热 HKC:开机后台加载知识核心(装配 + HKC 多语言 embedding 模型 + 搜索索引 over 全量 KU),
    # 免得"重启后第一次知识查询"触发冷装配卡 ~1 分钟。后台守护线程跑、不阻塞启动;失败静默。
    _prewarm_hkc(_os_instance)
    if _hestia_mcp is not None:
        # 内置 MCP 服务器的 session_manager 需在 app lifespan 内 run()
        async with _hestia_mcp.session_manager.run():
            print("[HestiaOS API] ✓ 内置 MCP 服务器已挂载 /api/mcp/")
            yield
    else:
        yield
    if _broadcast_task:
        _broadcast_task.cancel()
        try:
            await _broadcast_task
        except asyncio.CancelledError:
            pass
    # 释放持有原生资源的子系统(浏览器专用线程/会话),避免进程无法干净退出
    if _os_instance is not None:
        await _os_instance.aclose()
    print("[HestiaOS API] 关闭中...")


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


# ── 内置 MCP 服务器(默认关;HESTIA_MCP_SERVER=1 开)──────────
# 把只读知识+记忆暴露成 MCP 工具,挂 /api/mcp/(自动受 X-API-Key 中间件保护)。
_hestia_mcp = None
_hestia_mcp_app = None
if os.environ.get("HESTIA_MCP_SERVER", "0").strip() in ("1", "true", "True"):
    from .mcp_server import build_hestia_mcp
    _mcp_agents = os.environ.get("HESTIA_MCP_AGENTS", "0").strip() in ("1", "true", "True")
    _hestia_mcp = build_hestia_mcp(lambda: _os_instance, expose_agents=_mcp_agents)
    _hestia_mcp_app = _hestia_mcp.streamable_http_app()
    if _mcp_agents:
        print("[HestiaOS API] ⚠ MCP 已暴露 Agent 调度(ask_hestia)——外部客户端可驱动你的认知 Agent")
    if not os.environ.get("HESTIA_API_KEY", "").strip():
        print("[HestiaOS API] ⚠ 内置 MCP 服务器已开启但未设 HESTIA_API_KEY —— "
              "对外暴露前务必设置,否则任何人可读你的知识/记忆")
    if os.environ.get("HESTIA_CORS_ORIGINS", "").strip() == "*":
        print("[HestiaOS API] ⚠ MCP 已开启但 CORS=* —— 生产建议用 HESTIA_CORS_ORIGINS 收紧允许来源")

app = FastAPI(
    title="Hestia OS API",
    version=__version__,
    description="Cognitive Agent Operating System",
    lifespan=lifespan,
)
if _hestia_mcp_app is not None:
    app.mount("/api/mcp", _hestia_mcp_app)   # 受 _api_key_guard 保护(startswith /api)

# ── 可选鉴权 + CORS 收紧（部署安全）──────────────────────────
# HESTIA_API_KEY 未设 → 开放(本地默认,保持现状,既有测试/前端不受影响)。
# 设了 → 所有 /api/* 请求必须带正确的 X-API-Key,否则 401。
# 注意:http 中间件**看不到 websocket scope**,故 /ws 的鉴权在 WS 端点内单独做
# (见 harness_websocket,用 ?key= 查询参数)。前端(静态)与 /docs 始终开放。
# 默认只放行回环来源(本地开发足够:前端与 API 是同源挂载,同源请求本就不经 CORS)。
# 需要跨源时设 HESTIA_CORS_ORIGINS=来源1,来源2(逗号分隔);显式设 "*" 才会全开(不推荐,
# 与未鉴权叠加等于把 API 交给任意网页)。收紧默认值属于「默认安全」:不再默认 allow_origins=["*"]。
_cors_env = os.environ.get("HESTIA_CORS_ORIGINS", "").strip()
if _cors_env == "*":
    _CORS_ORIGINS: list[str] = ["*"]
    _CORS_ORIGIN_REGEX: str | None = None
elif _cors_env:
    _CORS_ORIGINS = [o.strip() for o in _cors_env.split(",") if o.strip()]
    _CORS_ORIGIN_REGEX = None
else:
    _CORS_ORIGINS = []
    _CORS_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_origin_regex=_CORS_ORIGIN_REGEX,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _api_key_guard(request, call_next):
    """设了 HESTIA_API_KEY 时,保护 /api/* 与 /ws/*。未设则放行(本地默认)。
    每请求读环境变量:可运行时配置,也便于测试。"""
    api_key = os.environ.get("HESTIA_API_KEY", "").strip()
    if api_key:
        path = request.url.path
        # /knowledge/* 是 HKC 星球的数据面(graph/ingest/reset),必须同 /api 一起鉴权,
        # 否则 ingest/reset/graph 对公众开放(泄密+可乱写)。/ws 由端点内部鉴权。
        if path.startswith("/api") or path.startswith("/knowledge"):
            # CORS 预检(OPTIONS + Access-Control-Request-Method)按规范**不携带**自定义请求头,
            # 浏览器也不会给它带 X-API-Key。必须放行交给 CORSMiddleware 处理;否则一旦启用鉴权,
            # 跨源请求会在预检阶段被 401 拦死(且响应无 CORS 头,浏览器只报 CORS 错误,极难定位)。
            if request.method == "OPTIONS" and request.headers.get("access-control-request-method"):
                return await call_next(request)
            import hmac
            # 常量时间比对,防对 API Key 的计时攻击(MCP 暴露后更重要)
            if not hmac.compare_digest(request.headers.get("X-API-Key", "") or "", api_key):
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    {"error": "unauthorized: 缺少或无效的 X-API-Key"}, status_code=401)
    return await call_next(request)


# ── 统一 API 网关:per-client 限流 + 配额(把散落的单点限流收敛成一层)──────────
from .core.gateway import Gateway
_GATEWAY = Gateway(
    rate_per_min=int(os.environ.get("HESTIA_GATEWAY_RATE", "600")),
    quota_per_hour=int(os.environ.get("HESTIA_GATEWAY_QUOTA", "20000")),
)
# 请求体硬顶(始终生效,不依赖限流开关):防超大 body 在解析/sanitize 前就吃满内存。
# 最大合法体是文件摄入(路由内 20MB 上限),故 32MB 顶对正常流量无感。
_MAX_BODY = int(os.environ.get("HESTIA_MAX_BODY_MB", "32")) * 1024 * 1024


def _gateway_on() -> bool:
    """网关是否启用。沿用鉴权哲学:配了 HESTIA_API_KEY(=对外暴露)才自动开;
    HESTIA_GATEWAY=1 强制开、=0 强制关。每请求读环境变量(运行时可配 + 便于测试)。"""
    v = os.environ.get("HESTIA_GATEWAY", "").strip()
    if v == "1":
        return True
    if v == "0":
        return False
    return bool(os.environ.get("HESTIA_API_KEY", "").strip())


@app.middleware("http")
async def _gateway_mw(request, call_next):
    """统一网关:只管 /api 与 /knowledge 数据面(探针 /health、/metrics、/ 不在其下)。
    client 身份:持有效 key → 按 key 计;否则按来源 IP 计(故轮换错误 key 无法绕过 IP 限流)。
    超限 → 429 + Retry-After。默认关(本地);配鉴权或显式开启才生效。"""
    path = request.url.path
    guarded = path.startswith("/api") or path.startswith("/knowledge")
    # ① 请求体大小硬顶(始终生效):Content-Length 超限 → 413,在读 body 前拦下
    if guarded:
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > _MAX_BODY:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                {"error": f"payload too large (>{_MAX_BODY // 1048576}MB)"}, status_code=413)
    # ② per-client 限流 + 配额(网关启用时)
    if guarded and _gateway_on():
        valid = os.environ.get("HESTIA_API_KEY", "").strip()
        hdr = (request.headers.get("X-API-Key") or "").strip()
        client = ("k:" + hdr) if (valid and hdr == valid) \
            else ("ip:" + (request.client.host if request.client else "anon"))
        allowed, retry, reason = _GATEWAY.check(client)
        if not allowed:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                {"error": f"rate limited ({reason})", "retry_after": retry},
                status_code=429, headers={"Retry-After": str(retry)})
    return await call_next(request)


@app.middleware("http")
async def _trace_mw(request, call_next):
    """按请求开一条 trace(contextvars 会随 asyncio.to_thread 传到后台线程,故 Agent/LLM/
    skill 里的 span 仍归属本次请求)。响应带 X-Trace-Id 便于定位。只追数据面,不污染静态/探针。"""
    from .core.tracing import start_trace, clear_trace, TRACES, parse_traceparent
    path = request.url.path
    if not (path.startswith("/api") or path.startswith("/knowledge")):
        return await call_next(request)
    # 跨进程接链:外部(如经 /api/mcp 调进来的 Agent)带了 W3C traceparent 就复用其 trace_id,
    # 这样两边的链路能对齐;非法/缺失则自己开一条(不信外部输入)。
    inbound = parse_traceparent(request.headers.get("traceparent"))
    tr = start_trace(f"{request.method} {path}", trace_id=inbound)
    try:
        resp = await call_next(request)
        try:
            resp.headers["X-Trace-Id"] = tr.trace_id
        except Exception:
            pass
        return resp
    finally:
        tr.finish()
        TRACES.put(tr)
        clear_trace()


@app.middleware("http")
async def _metrics_mw(request, call_next):
    """埋点:记录每个请求的延迟 + 状态(按路由模板分组,ID 不炸表)。"""
    import time as _t
    t0 = _t.perf_counter()
    status = 500
    try:
        resp = await call_next(request)
        status = resp.status_code
        return resp
    finally:
        ms = (_t.perf_counter() - t0) * 1000.0
        # 只按已匹配的路由模板分组;未匹配(404)统一记 "unmatched",防攻击者刷不同路径撑爆 key
        route = request.scope.get("route")
        ep = getattr(route, "path", None) or "unmatched"
        try:
            from .core.metrics import METRICS
            METRICS.record_request(f"{request.method} {ep}", status, ms)
        except Exception:
            pass


# ── HKC 星球数据面:挂载 HKC 自带的 /knowledge REST 路由 ──────────────────
# 星球 UI(frontend/planet)需要 /knowledge/graph、/ku/{id}、/ku/{id}/neighbors、
# /ingest/*、/reset 等。HKC 在本进程装配,这里把它自带的 knowledge 路由挂上,并用
# dependency_overrides 把 get_container 指向 **HestiaOS 已装配的同一个容器**(依赖倒置,
# 不另开一套;保持惰性:首个 /knowledge 请求才触发 HKC 装配)。鉴权由上面中间件覆盖 /knowledge。
# 缺 vendor/hkc 或导入失败 → 静默跳过(星球无数据但不影响主应用)。
try:
    from hestia.knowledge import _ensure_hkc_importable as _ensure_hkc
    _ensure_hkc()
    from hkc_api.routes.knowledge import router as _hkc_knowledge_router
    from hkc_api.container import get_container as _hkc_get_container

    def _resolve_hkc_container():
        svc = _os_instance.hkc if _os_instance is not None else None
        if svc is None or not getattr(svc, "available", False):
            raise HTTPException(503, "HKC 未启用(未 vendor 或缺依赖)")
        return svc.container

    app.dependency_overrides[_hkc_get_container] = _resolve_hkc_container
    app.include_router(_hkc_knowledge_router)
    # 星球副面板(冲突裁决/能力编译/最短路径)用的 HKC 路由。不挂的话 /conflicts、
    # /abilities、/search/path 在 hestiaOS 下 404 → 星球「冲突裁决」面板拉不到数据、
    # 永远显示 0(误导),能力/路径面板同样空。这里补挂(同一容器 override 已覆盖)。
    try:
        from hkc_api.routes.ability import router as _hkc_ability_router
        from hkc_api.routes.search  import router as _hkc_search_router
        app.include_router(_hkc_ability_router)   # /abilities, /conflicts
        app.include_router(_hkc_search_router)     # /search/path 等
        _sub = "/abilities, /conflicts, /search/*"
    except Exception as _e2:
        _sub = f"(能力/搜索路由未挂: {_e2})"
    print(f"[HestiaOS API] ✓ HKC 星球数据面已挂载 (/knowledge/*, {_sub})")
except Exception as _e:
    print(f"[HestiaOS API] · HKC 星球数据面未挂载(可选): {_e}")


# ── 请求模型 ───────────────────────────────────────────────

class TaskRequest(BaseModel):
    task: str
    agent_id: str = "supervisor"
    context: dict = {}
    images: list = []       # 多模态对话:[{media_type,data(base64)}];仅 /chat/stream 消费
    history: list = []      # 多轮上下文:[{role:'user'/'assistant',content}];前端带最近几轮,
                            # 后端拼进 LLM messages,让对话连贯(否则每轮无状态、记不住上一句)
    session_id: str = ""    # 会话归属:非空则本轮问答落盘到该会话(供"会话历史"回看)


class GoalRequest(BaseModel):
    agent_id: str
    goal: str
    allowed_tools: list[str] = []
    entities: list[str] = []


# ── 系统状态 ───────────────────────────────────────────────

@app.get("/api/status")
async def get_system_status():
    """获取所有 Agent 的状态"""
    return _os_instance.get_all_status()


@app.get("/api/tokens")
async def get_token_summary():
    """Token 消耗摘要(成本可观测性):总量、按 Agent、按模型。"""
    meter = getattr(_os_instance, "token_meter", None)
    if meter is None:
        return {"error": "token meter 未启用"}
    return {
        "summary": meter.summary(),
        "by_agent": meter.by_agent(),
        "by_provider": meter.by_provider(),
    }


@app.get("/api/tokens/report")
async def get_token_report():
    """Token 消耗的人类可读文本报告。"""
    meter = getattr(_os_instance, "token_meter", None)
    if meter is None:
        return {"error": "token meter 未启用"}
    return {"report": meter.report()}


# ============ AHS 执行链(Agent Harness Skills 标准)============

class AHSRunPayload(BaseModel):
    task: str
    confirmed: bool = False


@app.get("/api/ahs/skills")
async def ahs_list_skills():
    """列出注册的 skill(含信任分级 trust + 封杀标记 blocked)。"""
    pol = _os_instance.skill_policy
    out = _os_instance.skill_registry.catalog()
    for s in out:
        s["blocked"] = pol.is_blocked(s.get("id", ""))
    return {"skills": out}


class PolicyDenyPayload(BaseModel):
    id: str


@app.get("/api/ahs/policy")
async def ahs_policy_get():
    """全局封杀策略快照(deny/allow)。"""
    return _os_instance.skill_policy.snapshot()


@app.post("/api/ahs/policy/deny")
async def ahs_policy_deny(p: PolicyDenyPayload):
    """封杀一个技能 id(deny 压倒 enable,立即对所有执行/注入生效)。"""
    _os_instance.skill_policy.deny(p.id)
    return {"ok": True, "policy": _os_instance.skill_policy.snapshot()}


@app.delete("/api/ahs/policy/deny/{skill_id}")
async def ahs_policy_undeny(skill_id: str):
    """解封。E1:被 policy 层(管理员 HESTIA_POLICY_DENY / policy.json)锁定的技能运行时无法解封,
    诚实返回 ok=False + 原因(不再假报成功);其余情况与改前一致(ok=True)。"""
    _os_instance.skill_policy.undeny(skill_id)
    snap = _os_instance.skill_policy.snapshot()
    if skill_id in snap.get("policy_deny", []):
        return {"ok": False,
                "reason": f"「{skill_id}」被管理员策略(policy 层)锁定,运行时无法解封", "policy": snap}
    return {"ok": True, "policy": snap}


class SkillInstallPayload(BaseModel):
    id: str
    name: str
    description: str = ""
    kind: str                                  # 只允许 "web_api" | "mcp"
    triggers: list = []
    side_effect: str = "read"                  # read/write/send/pay
    destructive: bool = False
    external: bool = True
    params: dict = {}
    # web_api
    endpoint: str = ""
    http_method: str = "GET"
    http_headers: dict = {}
    param_location: str = "auto"
    allow_local: bool = False
    timeout: float = 10.0
    # mcp
    mcp_server: str = ""
    mcp_tool: str = ""
    acknowledged: bool = False                 # warn 级风险,用户确认后置 true


class SkillPatchPayload(BaseModel):
    enabled: bool


class SkillTestPayload(BaseModel):
    args: dict = {}
    confirmed: bool = False
    mode: str = "strict"


_SKILL_KINDS = {"web_api", "mcp"}
_SIDE_EFFECTS = {"read", "write", "send", "pay"}


def _validate_install(p: "SkillInstallPayload"):
    if p.kind not in _SKILL_KINDS:
        raise HTTPException(400, "kind 只能是 web_api 或 mcp(不支持从界面安装本地代码技能)")
    if p.side_effect not in _SIDE_EFFECTS:
        raise HTTPException(400, "side_effect 非法")
    if p.kind == "web_api":
        if not (p.endpoint.startswith("http://") or p.endpoint.startswith("https://")):
            raise HTTPException(400, "web_api 需合法 http(s) endpoint")
    if p.kind == "mcp" and not (p.mcp_server and p.mcp_tool):
        raise HTTPException(400, "mcp 需同时提供 mcp_server 与 mcp_tool")


@app.post("/api/ahs/skills")
async def ahs_install_skill(p: SkillInstallPayload):
    """安装/注册一个 web_api|mcp 技能(过安装审核门 + 持久化)。同 id 覆盖更新。"""
    _validate_install(p)
    from hestia.skills import SkillSpec
    from hestia.skills.review import review_install
    reg = _os_instance.skill_registry
    _existing = reg.get(p.id)
    if _existing is not None and _existing.source == "builtin":
        raise HTTPException(409, "该 id 属于内置技能,不能覆盖")
    existed = _existing is not None
    spec = SkillSpec(
        id=p.id, name=p.name, description=p.description, kind=p.kind,
        triggers=list(p.triggers), side_effect=p.side_effect,
        destructive=p.destructive, external=p.external, params=dict(p.params),
        endpoint=p.endpoint, http_method=p.http_method, http_headers=dict(p.http_headers),
        param_location=p.param_location, allow_local=p.allow_local, timeout=p.timeout,
        mcp_server=p.mcp_server, mcp_tool=p.mcp_tool, source="user",
    )
    verdict = review_install(spec)                       # 安装审核门
    if verdict["verdict"] == "deny":
        raise HTTPException(403, detail={"error": "install_denied", "reasons": verdict["reasons"]})
    if verdict["verdict"] == "warn" and not p.acknowledged:
        raise HTTPException(409, detail={"needs_ack": True, "warnings": verdict["warnings"]})
    reg.register(spec)
    _os_instance.skill_store.save(reg)
    return {"ok": True, "skill": spec.to_dict(), "created": not existed, "warnings": verdict["warnings"]}


@app.patch("/api/ahs/skills/{skill_id}")
async def ahs_patch_skill(skill_id: str, p: SkillPatchPayload):
    """启停一个技能(内置/用户均可,持久化)。"""
    reg = _os_instance.skill_registry
    sp = reg.get(skill_id)
    if sp is None:
        raise HTTPException(404, "技能不存在")
    sp.enabled = bool(p.enabled)
    _os_instance.skill_store.save(reg)
    return {"ok": True, "skill": sp.to_dict()}


@app.delete("/api/ahs/skills/{skill_id}")
async def ahs_delete_skill(skill_id: str):
    """删除用户安装的技能;内置技能不可删(只能停用)。"""
    reg = _os_instance.skill_registry
    sp = reg.get(skill_id)
    if sp is None:
        raise HTTPException(404, "技能不存在")
    if sp.source == "builtin":
        raise HTTPException(409, "内置技能不可删除,只能停用")
    reg.unregister(skill_id)
    _os_instance.skill_store.save(reg)
    return {"ok": True}


@app.post("/api/ahs/skills/{skill_id}/test")
async def ahs_test_skill(skill_id: str, p: SkillTestPayload):
    """单技能测试调用:走真实权限管线/断路器/SSRF。副作用未确认 → needs_confirmation。"""
    reg = _os_instance.skill_registry
    sp = reg.get(skill_id)
    if sp is None:
        raise HTTPException(404, "技能不存在")
    # 阻塞 I/O(web_api httpx / mcp 桥,最长数十秒)放线程池,勿堵死事件循环(否则慢工具冻全站)
    res = await asyncio.to_thread(_os_instance.skill_adapter.invoke, sp, p.args, p.confirmed, p.mode)
    return res.to_dict()


class McpServerPayload(BaseModel):
    id: str
    name: str = ""
    url: str
    headers: dict = {}
    transport: str = "streamable_http"     # streamable_http | sse
    acknowledged: bool = False


@app.post("/api/ahs/mcp/servers")
async def mcp_add_server(p: McpServerPayload):
    """接入一个远程 MCP server:审核→连接拉工具→批量注册 mcp 技能→持久化。"""
    from hestia.skills.review import review_mcp_server
    from hestia.skills.mcp_client import tool_to_spec
    if p.transport not in ("streamable_http", "sse"):
        raise HTTPException(400, "transport 只能是 streamable_http 或 sse")
    if not (p.url.startswith("http://") or p.url.startswith("https://")):
        raise HTTPException(400, "url 需为 http(s)")
    verdict = review_mcp_server(p.url)
    if verdict["verdict"] == "deny":
        raise HTTPException(403, detail={"error": "install_denied", "reasons": verdict["reasons"]})
    if verdict["verdict"] == "warn" and not p.acknowledged:
        raise HTTPException(409, detail={"needs_ack": True, "warnings": verdict["warnings"]})
    cfg = {"id": p.id, "name": p.name or p.id, "url": p.url,
           "headers": dict(p.headers), "transport": p.transport}
    try:
        tools = await asyncio.to_thread(_os_instance.mcp_manager.add_server, cfg)  # 桥阻塞→线程池
    except Exception as e:
        raise HTTPException(502, f"连接 MCP server 失败: {e}")
    reg = _os_instance.skill_registry
    n = 0
    for tool in tools:
        reg.register(tool_to_spec(p.id, tool)); n += 1
    _os_instance.skill_store.save(reg)
    _os_instance.mcp_store.save(_os_instance.mcp_manager.servers())
    return {"ok": True, "server": {k: cfg[k] for k in ("id", "name", "url", "transport")},
            "tools_registered": n, "warnings": verdict["warnings"]}


@app.get("/api/ahs/mcp/servers")
async def mcp_list_servers():
    """列出已接入 MCP server(工具数由注册表派生,无需连接)。"""
    reg = _os_instance.skill_registry
    all_sk = reg.all_including_disabled()
    out = []
    for c in _os_instance.mcp_manager.servers():
        tc = sum(1 for s in all_sk if s.kind == "mcp" and s.mcp_server == c["id"])
        out.append({"id": c["id"], "name": c["name"], "url": c["url"],
                    "transport": c["transport"], "tool_count": tc})
    return {"servers": out}


@app.delete("/api/ahs/mcp/servers/{server_id}")
async def mcp_remove_server(server_id: str):
    """移除 server + 其注册的所有 mcp 技能。"""
    mgr = _os_instance.mcp_manager
    if not mgr.remove_server(server_id):
        raise HTTPException(404, "server 不存在")
    reg = _os_instance.skill_registry
    removed = [s.id for s in reg.all_including_disabled()
               if s.kind == "mcp" and s.mcp_server == server_id]
    for sid in removed:
        reg.unregister(sid)
    _os_instance.skill_store.save(reg)
    _os_instance.mcp_store.save(mgr.servers())
    return {"ok": True, "removed_skills": len(removed)}


@app.post("/api/ahs/mcp/servers/{server_id}/refresh")
async def mcp_refresh_server(server_id: str):
    """重连拉工具:新增/更新技能,删除已消失的工具技能。"""
    from hestia.skills.mcp_client import tool_to_spec
    mgr = _os_instance.mcp_manager
    try:
        tools = await asyncio.to_thread(mgr.list_tools, server_id)  # 桥阻塞→线程池
    except KeyError:
        raise HTTPException(404, "server 不存在")
    except Exception as e:
        raise HTTPException(502, f"刷新失败: {e}")
    reg = _os_instance.skill_registry
    fresh = set()
    for tool in tools:
        spec = tool_to_spec(server_id, tool); reg.register(spec); fresh.add(spec.id)
    for s in [s for s in reg.all_including_disabled()
              if s.kind == "mcp" and s.mcp_server == server_id and s.id not in fresh]:
        reg.unregister(s.id)
    _os_instance.skill_store.save(reg)
    return {"ok": True, "tools": len(fresh)}


class MdSkillPayload(BaseModel):
    content: str = ""
    package_b64: str = ""            # OpenClaw 包(zip)的 base64;有则优先,解包取 SKILL.md
    id: str = ""
    acknowledged: bool = False


class MdSkillPatchPayload(BaseModel):
    enabled: bool | None = None
    triggers: list | None = None            # 改触发词(免解压重打包);None=不动


@app.post("/api/ahs/md-skills")
async def md_install(p: MdSkillPayload):
    """上传/注册一个 SKILL.md 指令技能(单 .md 文本 或 OpenClaw zip 包)。解析→审核→持久化。"""
    from hestia.skills.markdown_skill import parse_skill_md, extract_skill_package
    content, want_id, want_version = p.content, (p.id or None), ""
    if p.package_b64:
        import base64 as _b64
        try:
            content, meta = extract_skill_package(_b64.b64decode(p.package_b64))
        except ValueError as e:
            raise HTTPException(400, f"OpenClaw 包解析失败: {e}")
        except Exception as e:
            raise HTTPException(400, f"包解码失败: {e}")
        want_id = want_id or (meta.get("slug") or None)
        want_version = str(meta.get("version") or "")
    try:
        skill = parse_skill_md(content, id=want_id)
    except ValueError as e:
        raise HTTPException(400, f"SKILL.md 解析失败: {e}")
    if want_version and not skill.version:
        skill.version = want_version
    if not p.acknowledged:
        raise HTTPException(409, detail={"needs_ack": True, "warnings": [
            "此技能会把内容注入你 AI 的提示词、影响其行为,仅从可信来源安装"]})
    reg = _os_instance.md_skills
    existed = reg.get(skill.id) is not None
    reg.register(skill)
    _os_instance.md_store.save(reg)
    return {"ok": True, "skill": skill.to_meta(), "created": not existed}


@app.get("/api/ahs/md-skills")
async def md_list():
    pol = _os_instance.skill_policy
    out = _os_instance.md_skills.catalog()
    for s in out:
        s["blocked"] = pol.is_blocked(s.get("id", ""))
    return {"skills": out}


@app.get("/api/ahs/md-skills/{skill_id}")
async def md_get(skill_id: str):
    s = _os_instance.md_skills.get(skill_id)
    if s is None:
        raise HTTPException(404, "指令技能不存在")
    return s.to_dict()


@app.patch("/api/ahs/md-skills/{skill_id}")
async def md_patch(skill_id: str, p: MdSkillPatchPayload):
    reg = _os_instance.md_skills
    s = reg.get(skill_id)
    if s is None:
        raise HTTPException(404, "指令技能不存在")
    if p.enabled is not None:
        s.enabled = bool(p.enabled)
    if p.triggers is not None:
        s.triggers = [str(t).strip() for t in p.triggers if str(t).strip()]
    _os_instance.md_store.save(reg)
    return {"ok": True, "skill": s.to_meta()}


@app.delete("/api/ahs/md-skills/{skill_id}")
async def md_delete(skill_id: str):
    reg = _os_instance.md_skills
    if reg.get(skill_id) is None:
        raise HTTPException(404, "指令技能不存在")
    reg.unregister(skill_id)
    _os_instance.md_store.save(reg)
    return {"ok": True}


@app.post("/api/ahs/run")
async def ahs_run(payload: AHSRunPayload):
    """跑一遍 AHS 执行链:router→planner→composition,全程过 guard+breaker+hooks。"""
    try:
        res = _os_instance.ahs_pipeline.run(payload.task, confirmed=payload.confirmed)
        return {
            "task": res.task, "route": res.route, "plan": res.plan,
            "composition": res.composition, "success": res.success,
            "summary": res.summary,
        }
    except Exception as e:
        return {"success": False, "summary": f"执行链异常: {e}"}


@app.get("/api/ahs/breaker")
async def ahs_breaker_status():
    """断路器状态:各 skill 的失败计数与开闸情况。"""
    br = _os_instance.skill_adapter.breaker
    return {"breakers": {sid: br.status(sid) for sid in br._entries}}


class SchedulePreviewPayload(BaseModel):
    task: str


@app.post("/api/scheduler/preview")
async def scheduler_preview(payload: SchedulePreviewPayload):
    """认知调度预览:给一个任务,返回调度决策(优先级/意图/路由/抢占),不实际执行。"""
    from hestia.core.scheduler import CognitiveScheduler, SystemContext
    sch = CognitiveScheduler()
    d = sch.schedule(payload.task, SystemContext())
    return d.to_dict()


@app.get("/api/bus/history")
async def bus_history(channel: str = None, limit: int = 30):
    """系统事件总线历史:观测执行→审计→记忆的真值流转。"""
    try:
        return {"events": _os_instance.system_bus.history(channel, limit)}
    except Exception:
        return {"events": []}


@app.get("/api/truth/last")
async def truth_last():
    """真值管线最近一次的 trace 与记忆更新。"""
    tp = _os_instance.truth_pipeline
    return {"last_trace": tp.last_trace, "last_memory_update": tp.last_memory_update}


@app.get("/api/processes")
async def list_processes(state: str = None, limit: int = 30):
    """进程列表:观测任务作为"进程"的生命周期状态。"""
    try:
        return {"processes": _os_instance.process_manager.list(state, limit),
                "stats": _os_instance.process_manager.stats()}
    except Exception:
        return {"processes": [], "stats": {}}


@app.get("/api/processes/{process_id}")
async def get_process(process_id: str):
    """单个进程详情:含完整状态历史(state_history)。"""
    rec = _os_instance.process_manager.get(process_id)
    return rec.to_dict() if rec else {"error": "not_found"}


@app.get("/api/cognitive/state")
async def cognitive_state():
    """认知状态快照:系统此刻在想什么(目标/焦点/注意力/激活记忆/活跃进程)。"""
    try:
        return _os_instance.cognitive_state.snapshot()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/ahs/hooks/audit")
async def ahs_hooks_audit():
    """钩子审计日志(最近 20 条)。"""
    return {"audit": _os_instance.skill_adapter.hooks.recent_audit(20)}


@app.get("/api/memory/stats")
async def get_memory_stats():
    """记忆中心统计:供前端 vecRecall 面板展示真实数字。"""
    try:
        return _os_instance.hmr.stats()
    except Exception:
        return {"backend": "?", "total_memories": 0, "relations": 0}


# ── 会话历史 ───────────────────────────────────────────────
class SessionCreate(BaseModel):
    title: str = ""
    agent_id: str = "supervisor"


class SessionRename(BaseModel):
    title: str


@app.get("/api/sessions")
async def list_sessions(limit: int = 50):
    """会话列表(按更新时间倒序,轻字段不含 messages)。"""
    try:
        return {"sessions": _os_instance.sessions.list(limit)}
    except Exception:
        return {"sessions": []}


@app.post("/api/sessions")
async def create_session(req: SessionCreate):
    """新建会话。"""
    return _os_instance.sessions.create(req.title, req.agent_id)


@app.get("/api/sessions/{sid}")
async def get_session(sid: str):
    """取单个会话(含完整 messages),供前端恢复对话。"""
    d = _os_instance.sessions.get(sid)
    if d is None:
        raise HTTPException(404, "会话不存在")
    return d


@app.post("/api/sessions/{sid}/rename")
async def rename_session(sid: str, req: SessionRename):
    return {"ok": _os_instance.sessions.rename(sid, req.title)}


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str):
    return {"ok": _os_instance.sessions.delete(sid)}


@app.get("/api/memory/recent")
async def get_recent_memories(limit: int = 20):
    """最近的记忆条目(倒序),供前端右栏"记忆信息条"。轻字段,不含 embedding。"""
    try:
        fn = getattr(_os_instance.hmr, "recent", None)
        return {"memories": fn(limit) if callable(fn) else []}
    except Exception:
        return {"memories": []}


@app.get("/api/preferences")
async def list_preferences():
    """列出已记住的用户偏好(供查看/清理误存)。偏好是 shared,任一 Agent 视角一致,
    这里用 assistant 取全量。返回 [{id, content, ...}]。"""
    ag = _os_instance.assistant
    return {"preferences": ag.list_preferences()}


@app.delete("/api/preferences/{pref_id}")
async def delete_preference(pref_id: str):
    """删一条误存的用户偏好(F3:防误报偏好持久注入污染)。返回 {ok}。"""
    ag = _os_instance.assistant
    return {"ok": ag.delete_preference(pref_id)}


@app.get("/api/model")
async def get_model():
    """当前模型路由状态 + 可用 provider 列表。"""
    return _os_instance.router.info()


@app.get("/api/config/amap")
async def get_amap_config():
    """下发前端高德 JSAPI Key + 安全密钥(从 .env 读取,不硬编码在前端)。"""
    return {
        "js_key": os.environ.get("AMAP_JS_KEY", ""),
        "js_scode": os.environ.get("AMAP_JS_SCODE", ""),
    }


@app.get("/api/system/info")
async def get_system_info():
    """系统级只读信息:版本、运行参数、各 Agent、模块状态(供设置面板展示)。"""
    agents = {}
    for aid, ag in _os_instance._agents.items():
        h = ag.harness
        agents[aid] = {
            "name": getattr(ag, "AGENT_NAME", aid),
            "shadow_mode": h.is_shadow_mode,
            "shadow_rounds": h.shadow_rounds,
            "run_count": getattr(h, "_run_count", 0),
        }
    return {
        "version": __version__,
        "data_dir": os.environ.get("HESTIA_DATA_DIR", "./hestia_data"),
        "shadow_rounds": int(os.environ.get("HESTIA_SHADOW_ROUNDS", "20")),
        "model": _os_instance.router.info(),
        "amap": {
            "web_key": bool(os.environ.get("AMAP_MAPS_API_KEY")),
            "js_key": bool(os.environ.get("AMAP_JS_KEY")),
            "js_scode": bool(os.environ.get("AMAP_JS_SCODE")),
        },
        "agents": agents,
    }


class ModelSwitchPayload(BaseModel):
    provider: str
    model: Optional[str] = None


@app.post("/api/model/switch")
async def switch_model(p: ModelSwitchPayload):
    """运行时切换模型 provider(多模型路由核心能力)。"""
    try:
        return _os_instance.router.switch(p.provider, p.model)
    except Exception as e:
        raise HTTPException(400, f"切换失败: {e}")


@app.get("/api/agents/{agent_id}/status")
async def get_agent_status(agent_id: str):
    agent = _os_instance.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} 不存在")
    return agent.get_status()


# ── 任务执行 ───────────────────────────────────────────────

@app.post("/api/task")
async def run_task(req: TaskRequest):
    """执行任务，返回结果 + ThoughtChain 摘要。

    把认知处理 offload 到工作线程:每请求的 CPU 密集同步工作(harness 语义嵌入等)不再
    阻塞事件循环,并发请求的嵌入计算可真正并行(torch/ST CPU 前向释放 GIL),吞吐随核数扩展。
    · contextvars 随 to_thread 复制到工作线程 → 每请求会话状态仍隔离(见 base.py);
    · 内层 LLM 的 to_thread 用各自 asyncio.run 的独立事件循环 executor,不与外层竞争
      同一线程池,故无嵌套线程池死锁;
    · harness 累积态已在 harness.py 用 RLock 保护(嵌入在锁外,故并行不受锁影响)。"""
    def _work():
        # 经微内核调度器准入(默认无上限=直通;设了 HESTIA_MAX_CONCURRENCY 才背压)+ 生命周期事件
        return _os_instance.scheduler.run(
            lambda: asyncio.run(_os_instance.run(req.task, req.agent_id)),
            agent_id=req.agent_id, task=req.task)
    return await asyncio.to_thread(_work)


import threading as _threading
from .mcp_server import _RateLimiter as _RL
# 流式限流 + 并发上限(防刷爆/资源耗尽;env 可配)
_STREAM_LIMITER = _RL(int(os.environ.get("HESTIA_STREAM_RATE", "60")))          # 次/分
_STREAM_SEM = _threading.Semaphore(int(os.environ.get("HESTIA_STREAM_CONCURRENCY", "4")))


_MAX_IMAGES = 6
_MAX_IMG_B64 = 10 * 1024 * 1024        # 单图 base64 上限 ~10MB(≈7.5MB 原图)
_MAX_IMG_TOTAL = 20 * 1024 * 1024      # 所有图 base64 累计上限(防 6×10MB=60MB 聚合 payload)
_ALLOWED_IMG_MT = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def _sanitize_images(images):
    """校验/裁剪前端传来的多模态图片:限数量、限单图大小、限累计总量、白名单类型。
    防超大 payload/OOM(前端已缩放,这是对直连/恶意客户端的后端自保)。"""
    out = []
    total = 0
    if not isinstance(images, list):
        return out
    for im in images[:_MAX_IMAGES]:
        if not isinstance(im, dict):
            continue
        mt = str(im.get("media_type", "")).lower()
        d = im.get("data", "")
        if mt not in _ALLOWED_IMG_MT:
            continue
        if not isinstance(d, str) or not d or len(d) > _MAX_IMG_B64:
            continue
        if total + len(d) > _MAX_IMG_TOTAL:    # 累计超总上限 → 停(丢弃其余)
            break
        total += len(d)
        out.append({"media_type": mt, "data": d})
    return out


@app.post("/api/chat/stream")
async def chat_stream(req: TaskRequest, request: Request):
    """流式直答(方案A):SSE 逐字返回 Agent LLM 输出。用于对话/问答;走完整认知管线的任务执行
    用 /api/task(阻塞)或 /api/task/stream(流式,D1);此端点不跑 recall/verify/reflect 等 stage。
    背压:令牌桶限流 + 并发信号量;客户端断连 → stop_event 终止 LLM 流(省 token)。
    images:多模态对话,随问题发给视觉模型(非视觉模型 router 层报错→降级)。"""
    if not _STREAM_LIMITER.allow():
        async def _rl():
            yield "data: " + json.dumps({"error": "rate limited"}) + "\n\n"
        return StreamingResponse(_rl(), media_type="text/event-stream")

    agent = _os_instance._agents.get(req.agent_id) or _os_instance.assistant
    imgs = _sanitize_images(req.images)

    async def async_sse():
        if not _STREAM_SEM.acquire(blocking=False):   # 超并发上限 → 拒(不排队)
            yield "data: " + json.dumps({"error": "busy, try again later"}) + "\n\n"
            return
        q: asyncio.Queue = asyncio.Queue()
        stop = _threading.Event()
        loop = asyncio.get_running_loop()

        def _producer():
            _parts = []
            try:
                for chunk in agent.stream_reply(req.task, stop_event=stop, images=imgs, history=req.history):
                    _parts.append(chunk)
                    loop.call_soon_threadsafe(q.put_nowait, {"delta": chunk})
                loop.call_soon_threadsafe(q.put_nowait, {"done": True})
            except Exception as e:
                loop.call_soon_threadsafe(q.put_nowait, {"error": str(e)})
            finally:
                # 落盘会话历史(问 + 答),供"会话/所有会话"回看。断连中止也保留已生成部分。
                if req.session_id:
                    try:
                        _os_instance.sessions.append(req.session_id, "user", req.task)
                        _reply = "".join(_parts)
                        if _reply:
                            _os_instance.sessions.append(req.session_id, "assistant", _reply)
                    except Exception:
                        pass
                loop.call_soon_threadsafe(q.put_nowait, None)   # 结束哨兵

        _threading.Thread(target=_producer, daemon=True, name="chat-stream").start()
        try:
            while True:
                if await request.is_disconnected():
                    stop.set(); break                 # 断连 → 终止 LLM 流
                try:
                    item = await asyncio.wait_for(q.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if item is None:
                    break
                yield "data: " + json.dumps(item, ensure_ascii=False) + "\n\n"
        finally:
            stop.set()
            _STREAM_SEM.release()

    return StreamingResponse(async_sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/task/stream")
async def task_stream(req: TaskRequest, request: Request):
    """流式任务执行(D1):SSE 逐 stage 进度 + act 逐 token + 最终结果。用于任务/分析等走**完整
    认知管线**的场景(对话直答走 /api/chat/stream)。事件:`{stage}`/`{delta}`/`{result}`/`{error}`。
    背压与断连处理与 chat_stream 一致:断连 → stop_event 同时停管线 stage(P3)+ 停 act LLM 生成。"""
    if not _STREAM_LIMITER.allow():
        async def _rl():
            yield "data: " + json.dumps({"error": "rate limited"}) + "\n\n"
        return StreamingResponse(_rl(), media_type="text/event-stream")

    agent = _os_instance._agents.get(req.agent_id) or _os_instance.assistant
    # 流式跑完整认知管线,须是"模板 run_task(pipeline)"路径的领域 Agent。SupervisorAgent 重写了
    # run_task 做路由分派、且未实现 _execute(pipeline 的 act 会落空),故不走它 → 回落 assistant
    # (与 /api/chat/stream 一致:流式端点面向具体领域 Agent,不做主管路由)。
    if agent is _os_instance.supervisor:
        agent = _os_instance.assistant

    async def async_sse():
        if not _STREAM_SEM.acquire(blocking=False):   # 超并发上限 → 拒(不排队)
            yield "data: " + json.dumps({"error": "busy, try again later"}) + "\n\n"
            return
        q: asyncio.Queue = asyncio.Queue()
        stop = _threading.Event()
        loop = asyncio.get_running_loop()

        def _producer():
            try:
                for evt in agent.run_task_stream(req.task, stop_event=stop):
                    loop.call_soon_threadsafe(q.put_nowait, evt)
            except Exception as e:
                loop.call_soon_threadsafe(q.put_nowait, {"error": str(e)})
            finally:
                loop.call_soon_threadsafe(q.put_nowait, None)   # 结束哨兵

        _threading.Thread(target=_producer, daemon=True, name="task-stream-sse").start()
        try:
            while True:
                if await request.is_disconnected():
                    stop.set(); break                 # 断连 → 停管线 + 停 act LLM
                try:
                    item = await asyncio.wait_for(q.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if item is None:
                    break
                yield "data: " + json.dumps(item, ensure_ascii=False) + "\n\n"
        finally:
            stop.set()
            _STREAM_SEM.release()

    return StreamingResponse(async_sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/scheduler/stats")
async def scheduler_stats():
    """调度器记账:并发配额 + 提交/运行/完成/失败/峰值(可观测性)。"""
    return _os_instance.scheduler.stats()


# ── ThoughtChain 查询 ──────────────────────────────────────

@app.get("/api/thoughtchain/{agent_id}/chain")
async def get_thought_chain(agent_id: str, session_id: str = None):
    """获取 Agent 的推理链"""
    tc = _os_instance.thought_chain
    if session_id:
        nodes = tc.get_chain(session_id, agent_id)
    else:
        # 返回最近 20 个节点
        nodes = tc.get_blocked_nodes(agent_id, limit=20)
    return {"nodes": [n.to_dict() for n in nodes]}


@app.get("/api/thoughtchain/{agent_id}/blocked")
async def get_blocked_nodes(agent_id: str, limit: int = 20):
    """获取被 Harness 截断的节点（审计用）"""
    tc = _os_instance.thought_chain
    nodes = tc.get_blocked_nodes(agent_id, limit=limit)
    return {"blocked": [n.to_dict() for n in nodes], "count": len(nodes)}


@app.get("/api/thoughtchain/{agent_id}/trend")
async def get_drift_trend(agent_id: str, limit: int = 100):
    """获取漂移趋势数据（前端可视化用）"""
    tc = _os_instance.thought_chain
    trend = tc.get_delta_trend(agent_id, limit=limit)
    return {"trend": trend, "agent_id": agent_id}


@app.get("/api/thoughtchain/{agent_id}/summary")
async def get_thought_summary(agent_id: str):
    """获取 ThoughtChain 健康摘要"""
    return _os_instance.thought_chain.get_summary(agent_id)


# ── Harness 状态 ───────────────────────────────────────────

@app.get("/api/harness/{agent_id}/status")
async def get_harness_status(agent_id: str):
    """获取 Harness 状态：阈值、shadow mode、policy 摘要"""
    agent = _os_instance.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} 不存在")
    return {
        "agent_id": agent_id,
        "thresholds": agent.harness.thresholds,
        "policy": agent.harness.get_policy_summary(),
        "weights": agent.harness.weights,
    }


@app.get("/api/harness/{agent_id}/policy")
async def get_policy(agent_id: str):
    """获取 Agent 的 Policy YAML（完整内容）"""
    agent = _os_instance.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} 不存在")
    return agent.harness._policy


# ── 领域内核端点（B 阶段三内核）─────────────────────────────

class ImportPayload(BaseModel):
    csv_path: Optional[str] = None
    rows: Optional[list] = None     # 也可直接传记录数组


@app.get("/api/finance/analysis")
async def finance_analysis(period: Optional[str] = None,
                           current_balance: Optional[float] = None):
    """财务内核综合分析:预算/异常/订阅/烧钱/守门人裁决。"""
    try:
        return _os_instance.finance.analyze_finances(
            period=period, current_balance=current_balance)
    except Exception as e:
        raise HTTPException(500, f"财务分析失败: {e}")


@app.post("/api/finance/import")
async def finance_import(p: ImportPayload):
    """导入交易数据(CSV 路径或记录数组)。"""
    rt = _os_instance.finance_runtime
    try:
        if p.csv_path:
            n = rt.import_csv(p.csv_path)
        elif p.rows:
            from hestia.finance import Transaction
            n = 0
            for row in p.rows:
                rt.add_transaction(Transaction(**row), run_gatekeeper=False)
                n += 1
        else:
            return {"imported": 0, "note": "未提供数据"}
        return {"imported": n, "status": rt.status()}
    except Exception as e:
        raise HTTPException(400, f"导入失败: {e}")


@app.get("/api/learning/analysis")
async def learning_analysis():
    """学习内核综合分析:掌握度/遗忘风险/知识图谱/学习策略。"""
    try:
        return _os_instance.office.analyze_learning()
    except Exception as e:
        raise HTTPException(500, f"学习分析失败: {e}")


@app.post("/api/learning/import")
async def learning_import(p: ImportPayload):
    """导入知识点(办公学习内核)。learn() 内部已把内容存进 HKC(连续 ConceptKU,知识真源),
    不再另做桥接。"""
    rt = _os_instance.learning_runtime
    try:
        if p.csv_path:
            n = rt.import_csv(p.csv_path)
        elif p.rows:
            n = 0
            for row in p.rows:
                rt.learn(**row); n += 1
        else:
            return {"imported": 0, "note": "未提供数据"}
        rt.build_connections()
        return {"imported": n, "status": rt.status()}
    except Exception as e:
        raise HTTPException(400, f"导入失败: {e}")


@app.delete("/api/learning/item/{item_id}")
async def learning_delete(item_id: str):
    """删除一个学习知识点(内存+复习状态+图谱,并落盘)。"""
    rt = _os_instance.learning_runtime
    ok = rt.delete(item_id)
    return {"deleted": ok, "status": rt.status()}


class NotePayload(BaseModel):
    item_id: str
    note: str


@app.post("/api/learning/note")
async def learning_note(p: NotePayload):
    """给一个学习知识点追加一条笔记(有内容才存;没新增不写盘)。"""
    rt = _os_instance.learning_runtime
    r = rt.add_note(p.item_id, p.note)
    if "error" in r:
        raise HTTPException(400, r["error"])
    return r


@app.get("/api/itinerary/plan")
async def itinerary_plan(mode: str = "driving"):
    """行程内核规划:路线优化/可达性/冲突/守门人 + 高德可视化数据。"""
    try:
        return _os_instance.admin.plan_itinerary(mode=mode)
    except Exception as e:
        raise HTTPException(500, f"行程规划失败: {e}")


@app.post("/api/itinerary/import")
async def itinerary_import(p: ImportPayload):
    """导入行程点。"""
    rt = _os_instance.itinerary_runtime
    try:
        if p.csv_path:
            n = rt.import_csv(p.csv_path)
        elif p.rows:
            n = 0
            for row in p.rows:
                rt.add_waypoint(**row); n += 1
        else:
            return {"imported": 0, "note": "未提供数据"}
        return {"imported": n, "status": rt.status()}
    except Exception as e:
        raise HTTPException(400, f"导入失败: {e}")


# ── 领域导入:超越 csv_path，支持上传 csv / xlsx / json ─────────────────────
# 三领域运行时的 import_csv 是"列→类型转换"的唯一权威。多格式文件在此统一解析成 rows,
# 再桥接成运行时认识的临时 CSV → 复用 import_csv 的转换逻辑(不碰运行时,单一真源)。
_IMPORT_MAX_BYTES = 10 * 1024 * 1024
_IMPORT_COLUMNS = {
    "finance":   ["amount", "category", "entity", "account", "timestamp", "tags"],
    "learning":  ["content", "topic", "source", "tags"],
    "itinerary": ["name", "address", "lng", "lat", "start_time", "duration_min", "city"],
}


def _parse_import_rows(filename: str, data: bytes) -> list:
    """把上传的 csv / xlsx / json 统一解析成 [dict]。"""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".json":
        obj = json.loads(data.decode("utf-8"))
        rows = obj.get("rows") if isinstance(obj, dict) else obj
        if not isinstance(rows, list):
            raise ValueError("JSON 应为对象数组或 {rows:[...]}")
        return [dict(r) for r in rows if isinstance(r, dict)]
    if ext in (".csv", ".txt", ""):
        import csv as _csv
        import io as _io
        text = data.decode("utf-8-sig", errors="replace")
        return list(_csv.DictReader(_io.StringIO(text)))
    if ext == ".xlsx":
        import openpyxl as _openpyxl
        import io as _io
        wb = _openpyxl.load_workbook(_io.BytesIO(data), read_only=True, data_only=True)
        try:
            grid = list(wb.active.iter_rows(values_only=True))
        finally:
            wb.close()
        if not grid:
            return []
        headers = [str(h).strip() if h is not None else "" for h in grid[0]]
        out = []
        for r in grid[1:]:
            d = {headers[i]: ("" if v is None else v)
                 for i, v in enumerate(r) if i < len(headers) and headers[i]}
            if any(str(x).strip() for x in d.values()):
                out.append(d)
        return out
    raise ValueError(f"不支持的格式 {ext};支持 .csv .xlsx .json")


def _rows_to_temp_csv(rows: list, columns: list) -> str:
    """把任意来源 rows 写成运行时 import_csv 认识的 CSV(tags 列表 → | 连接),返回临时路径。"""
    import csv as _csv
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            rr = dict(r)
            if isinstance(rr.get("tags"), (list, tuple)):
                rr["tags"] = "|".join(str(t) for t in rr["tags"])
            w.writerow({c: ("" if rr.get(c) is None else rr.get(c)) for c in columns})
    return path


async def _domain_import_file(domain: str, runtime, file: UploadFile):
    """统一领域文件导入:解析 → 桥接 CSV → 运行时 import_csv(线程池,含阻塞 I/O)。"""
    data = await file.read()
    if len(data) > _IMPORT_MAX_BYTES:
        raise HTTPException(413, f"文件过大(上限 {_IMPORT_MAX_BYTES // 1048576}MB)")
    try:
        rows = _parse_import_rows(file.filename or "", data)
    except Exception as e:
        raise HTTPException(400, f"解析失败: {e}")
    if not rows:
        return {"imported": 0, "note": "文件无有效数据行"}
    tmp = _rows_to_temp_csv(rows, _IMPORT_COLUMNS[domain])
    try:
        n = await asyncio.to_thread(runtime.import_csv, tmp)
    except Exception as e:
        raise HTTPException(400, f"导入失败(检查列名是否匹配): {e}")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return {"imported": n, "status": runtime.status()}


@app.post("/api/finance/import/file")
async def finance_import_file(file: UploadFile = File(...)):
    """上传 csv/xlsx/json 导入交易。列:amount,category,entity,account,timestamp,tags。"""
    rt = _os_instance.finance_runtime
    return await _domain_import_file("finance", rt, file)


@app.post("/api/learning/import/file")
async def learning_import_file(file: UploadFile = File(...)):
    """上传 csv/xlsx/json 导入知识点。列:content,topic,source,tags。"""
    rt = _os_instance.learning_runtime
    res = await _domain_import_file("learning", rt, file)
    try:
        rt.build_connections()
    except Exception:
        pass
    return res


@app.post("/api/itinerary/import/file")
async def itinerary_import_file(file: UploadFile = File(...)):
    """上传 csv/xlsx/json 导入行程点。列:name,address,lng,lat,start_time,duration_min,city。"""
    rt = _os_instance.itinerary_runtime
    return await _domain_import_file("itinerary", rt, file)


# ── 主管编排端点（全局态势 + 跨 Agent)──────────────────────

@app.get("/api/orchestration/situation")
async def global_situation():
    """主管全局态势:三内核状态汇总 + 跨域告警(驾驶舱视图)。"""
    try:
        return _os_instance.supervisor.global_situation()
    except Exception as e:
        raise HTTPException(500, f"态势汇总失败: {e}")


class CrossReqPayload(BaseModel):
    from_agent: str
    to_agent: str
    request_type: str
    payload: dict
    reason: str = ""


@app.post("/api/orchestration/request")
async def cross_request(p: CrossReqPayload):
    """跨 Agent 请求:目标 Agent 用守门人裁决(如行程申请差旅预算)。"""
    try:
        return _os_instance.supervisor.cross_request(
            from_agent=p.from_agent, to_agent=p.to_agent,
            request_type=p.request_type, payload=p.payload, reason=p.reason)
    except Exception as e:
        raise HTTPException(500, f"跨 Agent 请求失败: {e}")


# ── 浏览器子系统(HIR-Browser)：AI 用的浏览器 ─────────────────
# 感知网页→压缩语义结构→执行动作→校验结果。async 安全(专用工作线程),
# playwright 缺失时结构化降级(available=False),不崩溃。受 /api 鉴权保护。

class BrowserActPayload(BaseModel):
    type: str                         # navigate|click|type|scroll|extract|wait
    target: str | None = None         # 元素编号;navigate 时为 URL
    value: object | None = None       # type 的文本;scroll 的方向
    meta: dict = {}


@app.get("/api/browser/status")
async def browser_status():
    """浏览器子系统能力/会话状态(available/session_active/launch_error)。"""
    return _os_instance.browser_service.status()


@app.get("/api/browser/perceive")
async def browser_perceive():
    """只感知当前页面语义结构(带编号的可交互元素 + 摘要),不改变页面。"""
    return await _os_instance.browser_service.perceive()


@app.post("/api/browser/act")
async def browser_act(p: BrowserActPayload):
    """执行一个 Web 动作,返回校验后最新语义结构。"""
    return await _os_instance.browser_service.act(p.model_dump())


# ── 知识核心(HKC)：同进程 DirectClient,惰性装配,缺依赖降级 ─────────
# HKC = Hestia Knowledge Core:KDE 抽取 → KEE 演化 → 混合检索(BM25+向量+图)。
# 首次命中任一路由时才装配(见 HestiaOS.hkc 惰性属性);未 vendor/缺依赖时 available=False。

class KnowledgeSearchPayload(BaseModel):
    query: str
    mode: str = "hybrid"          # bm25 | vector | graph | hybrid
    top_k: int = 10
    domain: str = ""


class KnowledgeIngestPayload(BaseModel):
    text: str
    source_title: str = ""
    domain: str = ""


def _hkc_or_none():
    """惰性取 HKCService;None 表示降级(未 vendor/缺依赖/装配失败)。"""
    try:
        return _os_instance.hkc
    except Exception:
        return None


@app.post("/api/knowledge/search")
async def knowledge_search(p: KnowledgeSearchPayload):
    """混合检索知识库。HKC 未启用或检索失败时返回空 hits(与整体降级哲学一致)。"""
    hkc = _hkc_or_none()
    if hkc is None or not hkc.available:
        return {"available": False, "hits": []}
    try:
        hits = await asyncio.to_thread(
            hkc.client.search, p.query, p.mode, p.top_k, p.domain)
    except Exception as e:
        return {"available": True, "hits": [], "error": str(e)}
    return {"available": True, "hits": [
        {"ku_id": h.ku_id, "score": h.score, "name": h.name,
         "summary": h.summary, "mode": h.mode, "ku_type": getattr(h, "ku_type", "")}
        for h in hits]}


@app.post("/api/knowledge/ingest")
async def knowledge_ingest(p: KnowledgeIngestPayload):
    """摄入文本 → KDE 抽取 KU → KEE 演化(守 Principle 07)。需配置 LLM;未配则抽取降级。"""
    hkc = _hkc_or_none()
    if hkc is None or not hkc.available:
        raise HTTPException(503, "HKC 未启用(未 vendor 或缺依赖)")
    try:
        res = await asyncio.to_thread(
            hkc.client.ingest_text, p.text, p.source_title, p.domain)
        return {"ku_count": res.ku_count, "ku_ids": res.ku_ids, "counts": res.counts}
    except Exception as e:
        raise HTTPException(500, f"摄入失败(通常因未配置 LLM): {e}")


# ── HKC 融合第 2 档 Step1:ACE 能力(只读查询面,无 LLM、零成本)─────────
@app.get("/api/knowledge/abilities")
async def knowledge_abilities():
    """列出可编译的能力 key(来自 ACE skill_taxonomy)。只读、确定性、无 LLM。"""
    hkc = _hkc_or_none()
    if hkc is None or not hkc.available:
        return {"available": False, "abilities": []}
    try:
        keys = await asyncio.to_thread(hkc.client.list_abilities)
    except Exception as e:
        return {"available": True, "abilities": [], "error": str(e)}
    return {"available": True, "abilities": list(keys)}


@app.get("/api/knowledge/ability/{ability_key}")
async def knowledge_ability(ability_key: str):
    """编译一个能力,返回 coverage/skills/workflows。**证据驱动 gate**:知识覆盖不足时
    返回 `sufficient=False` + coverage/missing_skills——这不是错误,是"知识还不足以具备该能力"
    (HestiaOS 证据驱动哲学:没根据的能力不予授予)。只读、无 LLM。"""
    hkc = _hkc_or_none()
    if hkc is None or not hkc.available:
        return {"available": False}
    try:
        ab = await asyncio.to_thread(hkc.client.compile_ability, ability_key)
    except Exception as e:
        # 覆盖不足(HKCInsufficientCoverage 带 coverage/missing_skills)→ 证据 gate,非错误
        if hasattr(e, "missing_skills"):
            return {"available": True, "sufficient": False,
                    "coverage": getattr(e, "coverage", {}),
                    "missing_skills": getattr(e, "missing_skills", []),
                    "reason": str(e)}
        raise HTTPException(404, f"能力编译失败(未知 key 或图错误): {e}")
    return {"available": True, "sufficient": True, "ability": {
        "ability_key": ab.ability_key, "display_name": ab.display_name,
        "domain": ab.domain, "coverage": ab.coverage, "version": ab.version,
        "skills": [{"skill_key": s.skill_key, "display_name": s.display_name,
                    "coverage": s.coverage, "concept_hits": s.concept_hits}
                   for s in ab.skills],
        "workflows": ab.workflows}}


# ── 认知搜索(HSR)：内部记忆 + 内部知识 + 联网,统一证据融合 ─────────
# 一次 search() 统一"查 HMR 记忆 / 查 HKC 知识 / 联网(百度)",走 planner→retriever→
# fusion→strategy 迭代循环(有上限),只产出证据图,不写任何持久化(边界:不代 Agent 决策)。

class SearchQueryPayload(BaseModel):
    query: str
    agent_id: str = "supervisor"
    context: dict = {}
    internal_only: bool = False   # True=只查内部(HMR+HKC+Agent态势),跳过联网


@app.post("/api/search/query")
async def search_query(p: SearchQueryPayload):
    """认知搜索(面向 AI/Agent 的证据管线):返回证据图 + 置信度 + 迭代轨迹。
    internal_only=True 时只查内部三源(记忆/知识/Agent),不联网。搜索内核惰性构造。"""
    try:
        rt = _os_instance.search_runtime
        result = await asyncio.to_thread(
            rt.search, p.query, p.agent_id, p.context, p.internal_only)
        return result.to_dict()
    except Exception as e:
        raise HTTPException(500, f"搜索失败: {e}")


# ── WebSocket：实时 Harness 事件流 ─────────────────────────

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


@app.websocket("/ws/harness")
async def harness_websocket(websocket: WebSocket):
    """
    实时 Harness 事件流。
    事件转发器在启动时已注册到线程安全队列,这里只负责接受连接并保活。

    鉴权:HTTP 中间件管不到 websocket scope,故 WS 的鉴权必须在端点内做。
    浏览器无法给 WS 握手设请求头,因此接受 ?key= 查询参数(也兼容能设头的非浏览器
    客户端的 X-API-Key 头)。未设 HESTIA_API_KEY 时开放。
    """
    api_key = os.environ.get("HESTIA_API_KEY", "").strip()
    if api_key:
        provided = (websocket.query_params.get("key")
                    or websocket.headers.get("x-api-key"))
        if provided != api_key:
            await websocket.close(code=1008)   # policy violation
            return
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # 保持连接
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ── 健康探活 ───────────────────────────────────────────────
# 标准探活端点(开放,不在鉴权前缀内、不触发 HKC 装配)。星球 UI 的 connect() 用它探活,
# 也供部署/监控用。放在静态挂载("/")之前,否则会被 StaticFiles 的 catch-all 吞成 404。
@app.get("/health")
async def health():
    """存活探针(liveness):浅、快,不触发子系统。"""
    return {"status": "ok", "service": "hestia-os", "version": __version__}


@app.get("/health/ready")
async def health_ready():
    """就绪探针(readiness):逐子系统深度检查。**开放**(探针无需鉴权),
    组件只回 ok/stub/down,不泄露 provider 名等细节。不触发 HKC 惰性初始化。"""
    o = _os_instance
    comps = {}
    comps["hmr"] = "ok" if getattr(o, "hmr", None) is not None else "down"
    _router = getattr(o, "router", None)
    if _router is None:
        comps["router"] = "down"
    elif getattr(_router, "is_stub", True):
        comps["router"] = "stub"
    else:
        comps["router"] = "ok"                       # 不回传 provider 名(开放端点,防泄露)
    comps["scheduler"] = "ok" if getattr(o, "scheduler", None) is not None else "down"
    hkc_svc = getattr(o, "_hkc_service", None)   # 只看已初始化的,不触发惰性 init
    comps["hkc"] = ("ok" if (hkc_svc is not None and getattr(hkc_svc, "available", False))
                    else ("uninitialized" if hkc_svc is None else "degraded"))
    comps["mcp_server"] = "on" if getattr(o, "mcp_manager", None) is not None else "off"
    down = [k for k, v in comps.items() if v == "down"]
    return {"status": "degraded" if down else "ok", "components": comps, "version": __version__}


@app.get("/api/metrics")
async def metrics_json():
    """可观测:各端点/LLM 的计数、错误率、延迟分位(p50/p95/avg)+ uptime。"""
    from .core.metrics import METRICS
    return METRICS.snapshot()


@app.get("/api/traces")
async def list_traces(limit: int = 30, persisted: bool = False,
                      slow_ms: float = None, errors_only: bool = False):
    """请求链路。默认看内存里最近若干条;persisted=1 查**已落库的历史**
    (可加 slow_ms / errors_only 过滤)——「找出昨天那次慢请求」走这里。"""
    from .core.tracing import TRACES
    limit = max(1, min(limit, 200))
    if persisted or slow_ms is not None or errors_only:
        return {"traces": TRACES.query(limit=limit, slow_ms=slow_ms,
                                       errors_only=errors_only), "source": "persisted"}
    return {"traces": TRACES.list(limit=limit), "source": "memory"}


@app.get("/api/traces/stats")
async def traces_stats():
    """OTLP 导出状态(未配 HESTIA_OTLP_ENDPOINT 则 enabled=false,完全不启用)。
    注意:本路由必须定义在 /api/traces/{trace_id} **之前**,否则 "stats" 会被当成 trace_id。"""
    from .core.tracing import OTLP
    return {"otlp": OTLP.stats()}


@app.get("/api/traces/{trace_id}")
async def get_trace(trace_id: str):
    """单条链路详情:各 span 的耗时/成败(定位这次慢在哪)。内存未命中则回落到库。"""
    from .core.tracing import TRACES
    d = TRACES.get_dict(trace_id)
    if d is None:
        raise HTTPException(404, "trace 不存在(内存环已滚出且未被采样落库)")
    return d


@app.get("/api/gateway/stats")
async def gateway_stats():
    """API 网关状态:是否启用 + 每分钟/每小时额度 + 当前追踪的 client 数 + 累计拒绝数。"""
    return {"enabled": _gateway_on(), **_GATEWAY.stats()}


@app.get("/metrics")
async def metrics_prometheus():
    """Prometheus 文本格式(供 scrape)。"""
    from .core.metrics import METRICS
    return PlainTextResponse(METRICS.prometheus_text())


# ── 前端静态文件 ───────────────────────────────────────────
# 前端就是 frontend/ 下的 deck.html + 两个 js,直接挂载该目录
frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    @app.get("/")
    async def serve_deck():
        # 新版 Hestia Deck(deck_v2);旧版仍可通过 /old 访问
        v2 = frontend_dir / "deck_v2.html"
        return FileResponse(str(v2 if v2.exists() else frontend_dir / "deck.html"))

    @app.get("/old")
    async def serve_deck_old():
        return FileResponse(str(frontend_dir / "deck.html"))

    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    # 默认只绑回环地址(本地开发)。对外暴露必须显式设 HESTIA_HOST,且应同时设置 HESTIA_API_KEY——
    # 否则该端口上任何可达者都能读写记忆、执行技能、摄入文件(这些端点全部无鉴权)。
    # 用 `python -m uvicorn hestia.api:app --host ...` 启动时会绕过本检查,故 lifespan 另有软提醒。
    _host = os.environ.get("HESTIA_HOST", "127.0.0.1").strip() or "127.0.0.1"
    _port = int(os.environ.get("HESTIA_PORT", "8000"))
    _LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
    if _host not in _LOOPBACK and not os.environ.get("HESTIA_API_KEY", "").strip():
        print(
            f"\n[HestiaOS API] ⚠⚠  绑定 {_host} 但未设置 HESTIA_API_KEY:\n"
            "    该端口上任何可达者都能读写记忆、执行技能、摄入文件。\n"
            "    请设置 HESTIA_API_KEY,或改用 HESTIA_HOST=127.0.0.1。\n"
            "    确认自担风险,可设 HESTIA_ALLOW_INSECURE=1 跳过此检查。\n"
        )
        if os.environ.get("HESTIA_ALLOW_INSECURE", "").strip() != "1":
            raise SystemExit("[HestiaOS API] 已拒绝启动:非回环绑定 + 无鉴权。")
    uvicorn.run(
        "hestia.api:app",
        host=_host,
        port=_port,
        reload=True,
    )
