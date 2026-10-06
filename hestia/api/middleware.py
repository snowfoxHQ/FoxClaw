"""API 中间件层:鉴权 / 统一网关限流 / 链路追踪 / 指标埋点。

从 hestia/api.py 原样搬移(纯搬移,零行为变更)。原先用 `@app.middleware("http")`
装饰器在 api 模块内直接注册;现集中到 `install_middleware(app)`,按**原注册顺序**
把 4 个 http 中间件挂到 app(注册顺序即执行顺序,保持不变)。
"""

import os

from ..core.gateway import Gateway


def _constant_time_eq(a: str, b: str) -> bool:
    """【P3-1】常量时间字符串比较,防计时攻击。

    攻击者可通过普通 == 的逐字符短路耗时差异逐位猜出 API Key(猜对前缀越长、
    响应越慢),从而把请求从 ip:* 身份伪造成 k:* 身份绕过 IP 限流。
    compare_digest 接受 bytes 或 ASCII str;API Key 可能含非 ASCII,故统一先
    encode("utf-8")。None 由调用方用 or "" 兜底。
    """
    import hmac
    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))


def _is_guarded_path(path: str) -> bool:
    """【P3-8】需要鉴权/限流/追踪的数据面路径:/api/*、/knowledge/*、/config/*。

    抽成辅助函数:此前 _api_key_guard / _gateway_mw / _trace_mw 各自重复
    `startswith("/api") or startswith("/knowledge")`,未来加新前缀(如 /ws 之外的
    新数据面)只改一处,也避免三处漂移出不一致的防护口径。
    /knowledge/* 是 HKC 数据面(graph/ingest/reset),必须与 /api 同一防护口径。
    /config/* 是 HKC 运行时 LLM 配置(连接设置),属可变状态,与数据面同口径保护。
    """
    return (path.startswith("/api") or path.startswith("/knowledge")
            or path.startswith("/config"))


async def _api_key_guard(request, call_next):
    """设了 HESTIA_API_KEY 时,保护 /api/* 与 /ws/*。未设则放行(本地默认)。
    每请求读环境变量:可运行时配置,也便于测试。"""
    api_key = os.environ.get("HESTIA_API_KEY", "").strip()
    if api_key:
        path = request.url.path
        # /knowledge/* 是 HKC 星球的数据面(graph/ingest/reset),必须同 /api 一起鉴权,
        # 否则 ingest/reset/graph 对公众开放(泄密+可乱写)。/ws 由端点内部鉴权。
        if _is_guarded_path(path):
            # CORS 预检(OPTIONS + Access-Control-Request-Method)按规范**不携带**自定义请求头,
            # 浏览器也不会给它带 X-API-Key。必须放行交给 CORSMiddleware 处理;否则一旦启用鉴权,
            # 跨源请求会在预检阶段被 401 拦死(且响应无 CORS 头,浏览器只报 CORS 错误,极难定位)。
            if request.method == "OPTIONS" and request.headers.get("access-control-request-method"):
                return await call_next(request)
            # 【P3-1】常量时间比对,防对 API Key 的计时攻击(MCP 暴露后更重要)
            if not _constant_time_eq(request.headers.get("X-API-Key", "") or "", api_key):
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    {"error": "unauthorized: 缺少或无效的 X-API-Key"}, status_code=401)
    return await call_next(request)


# ── 统一 API 网关:per-client 限流 + 配额(把散落的单点限流收敛成一层)──────────
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


async def _gateway_mw(request, call_next):
    """统一网关:只管 /api 与 /knowledge 数据面(探针 /health、/metrics、/ 不在其下)。
    client 身份:持有效 key → 按 key 计;否则按来源 IP 计(故轮换错误 key 无法绕过 IP 限流)。
    超限 → 429 + Retry-After。默认关(本地);配鉴权或显式开启才生效。"""
    path = request.url.path
    guarded = _is_guarded_path(path)
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
        # 【P3-1】常量时间比较:普通 == 的短路耗时可被用来逐位猜 key,
        # 把请求从 ip:* 伪造成 k:* 身份绕过 IP 限流
        client = ("k:" + hdr) if (valid and _constant_time_eq(hdr, valid)) \
            else ("ip:" + (request.client.host if request.client else "anon"))
        allowed, retry, reason = _GATEWAY.check(client)
        if not allowed:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                {"error": f"rate limited ({reason})", "retry_after": retry},
                status_code=429, headers={"Retry-After": str(retry)})
    return await call_next(request)


async def _trace_mw(request, call_next):
    """按请求开一条 trace(contextvars 会随 asyncio.to_thread 传到后台线程,故 Agent/LLM/
    skill 里的 span 仍归属本次请求)。响应带 X-Trace-Id 便于定位。只追数据面,不污染静态/探针。"""
    from ..core.tracing import start_trace, clear_trace, TRACES, parse_traceparent
    path = request.url.path
    if not _is_guarded_path(path):
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
            from ..core.metrics import METRICS
            METRICS.record_request(f"{request.method} {ep}", status, ms)
        except Exception:
            pass


def install_middleware(app):
    """按原注册顺序挂载 4 个 http 中间件(等价于原先的 @app.middleware 装饰)。

    顺序即执行顺序(Starlette 中间件后加先执行),必须与搬移前完全一致:
    _api_key_guard → _gateway_mw → _trace_mw → _metrics_mw。
    """
    app.middleware("http")(_api_key_guard)
    app.middleware("http")(_gateway_mw)
    app.middleware("http")(_trace_mw)
    app.middleware("http")(_metrics_mw)
