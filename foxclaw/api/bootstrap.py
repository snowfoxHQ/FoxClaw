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
    try:
        from foxclaw.knowledge import _ensure_hkc_importable as _ensure_hkc
        _ensure_hkc()
        from hkc_api.routes.knowledge import router as _hkc_knowledge_router
        from hkc_api.container import get_container as _hkc_get_container

        def _resolve_hkc_container():
            os_inst = _api.current_os()
            svc = os_inst.hkc if os_inst is not None else None
            if svc is None or not getattr(svc, "available", False):
                raise HTTPException(503, "HKC 未启用(未 vendor 或缺依赖)")
            return svc.container

        app.dependency_overrides[_hkc_get_container] = _resolve_hkc_container
        app.include_router(_hkc_knowledge_router)
        @app.get("/stats", tags=["system"])
        def _hkc_stats():
            return _resolve_hkc_container().stats()
        from hkc_api.routes.config import router as _hkc_config_router
        app.include_router(_hkc_config_router)
        try:
            from hkc_api.routes.ability import router as _hkc_ability_router
            from hkc_api.routes.search  import router as _hkc_search_router
            app.include_router(_hkc_ability_router)
            app.include_router(_hkc_search_router)
            _sub = "/abilities, /conflicts, /search/*"
        except Exception as _e2:
            _sub = f"(能力/搜索路由未挂: {_e2})"
        print(f"[Foxclaw API] ✓ HKC 星球数据面已挂载 (/knowledge/*, /config/*, {_sub})")
    except Exception as _e:
        print(f"[Foxclaw API] · HKC 星球数据面未挂载(可选): {_e}")


def _frontend_dir() -> Path | None:
    """前端根目录:**源码仓的 `frontend/` 优先**,回退包内 `foxclaw/web`。

    源码仓里 `frontend/` 与 `foxclaw/` 平级,是改前端的地方;`foxclaw/web/` 只是
    `tools/bundle_web.py` 在构建前复制的副本(已 gitignore)。顺序反过来的话,本地
    改完前端不重跑脚本,`serve` 会继续显示包内那份过期副本,很难排查。wheel 里没有
    仓库结构(`frontend/` 不存在),自然落到 `foxclaw/web/`。
    两个位置都要看 `v3/index.html` / `deck_v2.html` 是否真的在——只有 `__init__.py`
    的空目录不算,否则 mount 之后 `/` 会指向不存在的文件。
    """
    for d in (Path(__file__).resolve().parents[2] / "frontend",
              Path(__file__).resolve().parent.parent / "web"):
        if any((d / name).is_file() for name in
               ("v3/index.html", "deck_v2.html", "deck.html")):
            return d
    return None


def _mount_frontend(app):
    frontend_dir = _frontend_dir()
    if frontend_dir is None:
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
