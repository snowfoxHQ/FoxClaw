"""app 配置与挂载:CORS、HKC 星球数据面、前端静态文件。

从 `foxclaw/api/__init__.py` 搬移出来的「挂载类」逻辑,均为接收 `app` 参数的纯函数,
在 `__init__.py` 创建 `app` 之后按原顺序调用,保持行为不变。
"""
import os
from pathlib import Path

from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import foxclaw.api as _api


def _configure_cors(app):
    # ── 可选鉴权 + CORS 收紧（部署安全）──────────────────────────
    # FOXCLAW_API_KEY 未设 → 开放(本地默认,保持现状,既有测试/前端不受影响)。
    # 设了 → 所有 /api/* 请求必须带正确的 X-API-Key,否则 401。
    # 注意:http 中间件**看不到 websocket scope**,故 /ws 的鉴权在 WS 端点内单独做
    # (见 harness_websocket,用 ?key= 查询参数)。前端(静态)与 /docs 始终开放。
    # 默认只放行回环来源(本地开发足够:前端与 API 是同源挂载,同源请求本就不经 CORS)。
    # 需要跨源时设 FOXCLAW_CORS_ORIGINS=来源1,来源2(逗号分隔);显式设 "*" 才会全开(不推荐,
    # 与未鉴权叠加等于把 API 交给任意网页)。收紧默认值属于「默认安全」:不再默认 allow_origins=["*"]。
    _cors_env = os.environ.get("FOXCLAW_CORS_ORIGINS", "").strip()
    if _cors_env == "*":
        _CORS_ORIGINS = ["*"]
        _CORS_ORIGIN_REGEX = None
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


def _mount_hkc(app):
    # ── HKC 星球数据面:挂载 HKC 自带的 /knowledge REST 路由 ──────────────────
    # 星球 UI(frontend/planet)需要 /knowledge/graph、/ku/{id}、/ku/{id}/neighbors、
    # /ingest/*、/reset 等。HKC 在本进程装配,这里把它自带的 knowledge 路由挂上,并用
    # dependency_overrides 把 get_container 指向 **Foxclaw 已装配的同一个容器**(依赖倒置,
    # 不另开一套;保持惰性:首个 /knowledge 请求才触发 HKC 装配)。鉴权由上面中间件覆盖 /knowledge。
    # 缺 vendor/hkc 或导入失败 → 静默跳过(星球无数据但不影响主应用)。
    try:
        from foxclaw.knowledge import _ensure_hkc_importable as _ensure_hkc
        _ensure_hkc()
        from hkc_api.routes.knowledge import router as _hkc_knowledge_router
        from hkc_api.container import get_container as _hkc_get_container

        def _resolve_hkc_container():
            svc = _api._os_instance.hkc if _api._os_instance is not None else None
            if svc is None or not getattr(svc, "available", False):
                raise HTTPException(503, "HKC 未启用(未 vendor 或缺依赖)")
            return svc.container

        app.dependency_overrides[_hkc_get_container] = _resolve_hkc_container
        app.include_router(_hkc_knowledge_router)
        # /stats 系统统计:独立 hkc-api 在 main.py 顶层 @app.get("/stats") 定义,不在任何
        # router 里,嵌入式挂载时会漏掉 → 星球 UI 的 updateDataInfo 调 /stats 404,
        # 「数据目录 / 全库知识总数」提示失效。这里补挂,复用同一容器 override(惰性装配)。
        @app.get("/stats", tags=["system"])
        def _hkc_stats():
            return _resolve_hkc_container().stats()
        # 连接设置(星球 UI 左下的「连接设置」面板)POST /config/llm 做运行时 LLM 配置,
        # 覆盖 .env 的 HKC_LLM_*。独立 hkc-api 有这个路由,嵌入式 foxclaw 之前没挂 → 前端
        # 报「模型配置失败:404」且改配置无效。这里补挂 config 路由(同一容器 override 已覆盖),
        # 让「连接设置」的「知识抽取模型」段在 foxclaw 下真正生效。
        from hkc_api.routes.config import router as _hkc_config_router
        app.include_router(_hkc_config_router)   # /config/llm(GET 读配置 / POST 改配置)
        # 星球副面板(冲突裁决/能力编译/最短路径)用的 HKC 路由。不挂的话 /conflicts、
        # /abilities、/search/path 在 Foxclaw 下 404 → 星球「冲突裁决」面板拉不到数据、
        # 永远显示 0(误导),能力/路径面板同样空。这里补挂(同一容器 override 已覆盖)。
        try:
            from hkc_api.routes.ability import router as _hkc_ability_router
            from hkc_api.routes.search  import router as _hkc_search_router
            app.include_router(_hkc_ability_router)   # /abilities, /conflicts
            app.include_router(_hkc_search_router)     # /search/path 等
            _sub = "/abilities, /conflicts, /search/*"
        except Exception as _e2:
            _sub = f"(能力/搜索路由未挂: {_e2})"
        print(f"[Foxclaw API] ✓ HKC 星球数据面已挂载 (/knowledge/*, /config/*, {_sub})")
    except Exception as _e:
        print(f"[Foxclaw API] · HKC 星球数据面未挂载(可选): {_e}")


def _mount_frontend(app):
    # ── 前端静态文件 ───────────────────────────────────────────
    # frontend/ 下现在并存三套:frontend/v3(Vue,当前主界面)、deck_v2.html(旧主界面)、
    # deck.html(最早的一版)。根路径必须给 v3 —— 之前一直指向 deck_v2,新版只能手输 /v3/,
    # 等于新界面根本没上线。
    frontend_dir = Path(__file__).parent.parent.parent / "frontend"
    if not frontend_dir.exists():
        return

    v3_index = frontend_dir / "v3" / "index.html"

    @app.get("/")
    async def serve_root():
        """主界面。v3 未构建(没跑 npm run build)时退回旧 deck_v2,不让根路径 404。"""
        if v3_index.exists():
            return FileResponse(str(v3_index))
        legacy = frontend_dir / "deck_v2.html"
        return FileResponse(str(legacy if legacy.exists() else frontend_dir / "deck.html"))

    @app.get("/classic")
    async def serve_classic():
        """旧界面(deck_v2,v5.34):新版出问题时的手动回退入口,也留着它的二级面板历史。"""
        return FileResponse(str(frontend_dir / "deck_v2.html"))

    @app.get("/old")
    async def serve_deck_old():
        return FileResponse(str(frontend_dir / "deck.html"))

    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
