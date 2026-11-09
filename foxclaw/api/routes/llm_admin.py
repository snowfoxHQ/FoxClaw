"""LLM 模型管理 — 前端下拉式选模型 + 自定义模型(不污染 .env)。

设计要点
────────
- 自定义模型存到 ``FOXCLAW_DATA_DIR/custom_models.json``(API key 简单 base64 混淆,
  本地单用户足够 — 真实部署应换 secret manager)。
- 当前选中存到 ``FOXCLAW_DATA_DIR/.current_model``;``model_router`` 用 mtime 缓存
  做 lazy reload,**用户改完不用重启 foxclaw**。
- 所有 GET 都不返回完整 API key(只返回 ``has_key`` 布尔);POST ``/test`` 也不外泄 key。
"""
import os
import json
import time
from pathlib import Path
from typing import Optional, Dict

from fastapi import HTTPException
from pydantic import BaseModel

import foxclaw.api as _api
from .. import app
from ...core import secrets as _secrets
from ...core.model_router import normalize_base_url


_DATA_DIR = Path(os.environ.get("FOXCLAW_DATA_DIR", "./foxclaw_data"))
CUSTOM_MODELS_PATH = _DATA_DIR / "custom_models.json"
CURRENT_MODEL_PATH = _DATA_DIR / ".current_model"


# 预置 provider(展示用:标签 + 配色 + 探测 key 名)
_PRESET_PROVIDERS = [
    {"provider": "generic",   "label": "通用 OpenAI 兼容", "color": "#7e93b8"},
    {"provider": "anthropic", "label": "Claude",            "color": "#ff9d00"},
    {"provider": "openai",    "label": "GPT",               "color": "#00ffb2"},
    {"provider": "deepseek",  "label": "DeepSeek",          "color": "#5b8cff"},
    {"provider": "google",    "label": "Gemini",            "color": "#38bdf8"},
    {"provider": "qwen",      "label": "通义千问",          "color": "#ffc24b"},
    {"provider": "minimax",   "label": "MiniMax",           "color": "#a78bfa"},
    {"provider": "local",     "label": "本地模型",          "color": "#d99a3c"},
]

_PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "deepseek":  "DEEPSEEK_API_KEY",
    "google":    "GOOGLE_API_KEY",
    "qwen":      "DASHSCOPE_API_KEY",
    "minimax":   "MINIMAX_API_KEY",
    "local":     "LOCAL_LLM_BASE",
    "generic":   "GENERIC_LLM_API_KEY",
}


# ── 工具 ─────────────────────────────────────────────────

def _save_key(name: str, key: str) -> dict:
    """把 key 存到最安全的地方,返回该写进 custom_models.json 的字段。

    有 OS 凭据库 → 只留 `api_key_kr: True` 标记,JSON 里不再有任何可还原的密文;
    没有 → 保持 base64(导入/删除/无桌面 Linux 等场景)。两个键总是成对写,
    免得「旧密文 + 新标记」并存导致 has_key 与读取路径对不上。
    """
    key = (key or "").strip()
    if not key:
        return {"api_key_kr": False, "api_key_obf": ""}
    if _secrets.store(name, key):
        return {"api_key_kr": True, "api_key_obf": ""}
    return {"api_key_kr": False, "api_key_obf": _secrets.obf(key)}


def _has_key(m: dict) -> bool:
    """列表/详情只答「有没有 key」,绝不把 key 本身回给前端。"""
    return bool(m.get("api_key_obf") or m.get("api_key") or m.get("api_key_kr"))

def _load_custom() -> list:
    if not CUSTOM_MODELS_PATH.exists():
        return []
    try:
        return json.loads(CUSTOM_MODELS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []

def _save_custom(models: list):
    CUSTOM_MODELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CUSTOM_MODELS_PATH.write_text(
        json.dumps(models, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _sync_hkc_llm() -> None:
    """把「界面当前选中的模型」推给知识抽取(HKC KDE extractor)。

    病根:``init_hkc`` 只在启动时 ``_detect_llm()`` 一次,extractor 拿的是那一刻的快照。
    用户在界面切了模型(或换掉失效的 key)→ 主对话走 mtime lazy reload 立刻生效,
    知识抽取却还用旧的失效 key,于是摄入永远 401 → 0 条 KU → 复习板块永远 0 卡。
    extractor 自带 ``reconfigure``(HKC 自己的界面也用它),这里复用同一条路径。
    """
    try:
        os_inst = _api.current_os()
        hkc = os_inst.hkc if os_inst is not None else None
        if hkc is None or not getattr(hkc, "available", False):
            return
        from foxclaw.knowledge import _detect_llm
        provider, key, base, model = _detect_llm()
        hkc.container.kde.extractor.reconfigure(
            provider=provider,
            api_key=key or None,
            model=model or None,
            base_url=base or None,
        )
    except Exception as e:      # 同步失败绝不影响"切模型"这个主操作
        print(f"[llm_admin] 同步知识抽取(HKC)配置失败: {e}")


# ── 端点 ─────────────────────────────────────────────────

@app.get("/api/llm/models")
async def list_models():
    """列出所有可用模型:预置(按 .env 探测) + 自定义(从 custom_models.json) + 当前选中。"""
    available = []
    for p in _PRESET_PROVIDERS:
        env_key = _PROVIDER_KEY_ENV[p["provider"]]
        configured = bool(os.environ.get(env_key, "").strip())
        available.append({**p, "configured": configured, "source": "preset"})
    custom = []
    for m in _load_custom():
        custom.append({
            "name":     m.get("name", ""),
            "model":    m.get("model", ""),
            "base_url": m.get("base_url", ""),
            "note":     m.get("note", ""),
            "has_key":  _has_key(m),
            "source":   "custom",
        })
    current = None
    if CURRENT_MODEL_PATH.exists():
        try:
            current = json.loads(CURRENT_MODEL_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"providers": available, "custom": custom, "current": current}


class _SelectReq(BaseModel):
    source: str                  # "auto" | "preset" | "custom"
    provider: Optional[str] = None
    name: Optional[str] = None

@app.post("/api/llm/select")
async def select_model(req: _SelectReq):
    """记录用户选择 *并立即生效*。

    病根:此前只写 ``.current_model`` 就算成功 —— 若该自定义模型其实构造不出 client
    (key 空 / BASE URL 非法 / 记录已被删),前端显示"已切换",聊天却继续走旧 provider,
    用户看到的是"填了填不上"。现在选中前先自检,失败直接 400 带原因。
    """
    os_inst = _api.current_os()
    router = os_inst.router if os_inst is not None else None
    if req.source == "auto":
        if CURRENT_MODEL_PATH.exists():
            CURRENT_MODEL_PATH.unlink()
        if router is not None:
            router._maybe_reload_selection()
        _sync_hkc_llm()
        return {"ok": True, "current": None}
    if req.source == "preset":
        if not req.provider or req.provider not in _PROVIDER_KEY_ENV:
            raise HTTPException(400, f"未知 provider: {req.provider}")
        env_key = _PROVIDER_KEY_ENV[req.provider]
        if not os.environ.get(env_key, "").strip():
            raise HTTPException(400, f"{req.provider} 未在 .env 配置 Key({env_key})")
        data = {"source": "preset", "provider": req.provider}
    elif req.source == "custom":
        name = req.name
        if not name:
            raise HTTPException(400, "缺少自定义模型名称 name")
        models = _load_custom()
        match = next((m for m in models if m.get("name") == name), None)
        if not match:
            raise HTTPException(404, f"未找到自定义模型: {name}")
        # 预检:能不能真构造出 client(否则"切换成功"是假象)
        if router is not None:
            client, _model, err = router._build_custom_client(name)
            if client is None:
                raise HTTPException(400, f"该自定义模型不可用: {err}")
        data = {"source": "custom", "name": name, "model_id": match.get("model", "")}
    else:
        raise HTTPException(400, f"未知 source: {req.source}")
    CURRENT_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_MODEL_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    # 立即 reload(mtime 变了);正常聊天路径也会 lazy reload,这里只是让 /runtime 立刻正确
    if router is not None:
        router._maybe_reload_selection()
    _sync_hkc_llm()      # 知识抽取跟着切,别让它继续用旧 key
    return {"ok": True, "current": data,
            "runtime": ({"provider": router.provider,
                         "model": getattr(router, "model", ""),
                         "client_ok": getattr(router, "_client", None) is not None}
                        if router is not None else None)}


class _CustomAddReq(BaseModel):
    name: str
    base_url: str
    api_key: str
    model: str
    extra_headers: Optional[Dict[str, str]] = None
    group_id: Optional[str] = None
    note: Optional[str] = ""

@app.post("/api/llm/custom/add")
async def add_custom_model(req: _CustomAddReq):
    """添加自定义模型(不污染 .env,存到 FOXCLAW_DATA_DIR/custom_models.json)。"""
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "name 不能为空")
    if not req.base_url.strip() or not req.api_key.strip() or not req.model.strip():
        raise HTTPException(400, "base_url / api_key / model 都必填")
    try:
        base_url = normalize_base_url(req.base_url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    models = _load_custom()
    if any(m.get("name") == name for m in models):
        raise HTTPException(409, f"已存在同名模型: {name}")
    extra = dict(req.extra_headers or {})
    if req.group_id and req.group_id.strip():
        extra["MiniMax-Group-Id"] = req.group_id.strip()
    models.append({
        "name":          name,
        "base_url":      base_url,
        **_save_key(name, req.api_key),
        "model":         req.model.strip(),
        "extra_headers": extra,
        "note":          req.note or "",
        "created":       time.time(),
    })
    _save_custom(models)
    return {"ok": True, "name": name}


class _CustomUpdateReq(BaseModel):
    """编辑自定义模型:None / 空字符串 语义不同。
    - 字段没传(= None):该字段不动
    - 字段传了但空字符串:对 note/group_id/extra_headers 是 '清空' 语义;
      对 api_key 是 '保留原值'(防误清)
    - 字段传了非空:用新值覆盖
    """
    name:          Optional[str] = None
    base_url:      Optional[str] = None
    api_key:       Optional[str] = None
    model:         Optional[str] = None
    extra_headers: Optional[Dict[str, str]] = None
    group_id:      Optional[str] = None   # 显式空字符串 = 移除 MiniMax-Group-Id header
    note:          Optional[str] = None


@app.get("/api/llm/custom/{name}")
async def get_custom_model(name: str):
    """取单个自定义模型详情(供前端编辑表单预填)。
    api_key 永不出网 — 只返 ``has_key`` 布尔(与 list_models 一致)。"""
    models = _load_custom()
    match = next((m for m in models if m.get("name") == name), None)
    if not match:
        raise HTTPException(404, f"未找到: {name}")
    extra = dict(match.get("extra_headers") or {})
    gid = extra.get("MiniMax-Group-Id", "")
    # extra_headers 拆成 group_id + 其它(避免两边都返回重复键)
    extra_only = {k: v for k, v in extra.items() if k != "MiniMax-Group-Id"}
    return {
        "name":          match.get("name", ""),
        "base_url":      match.get("base_url", ""),
        "model":         match.get("model", ""),
        "has_key":       _has_key(match),
        "group_id":      gid,
        "extra_headers": extra_only,
        "note":          match.get("note", ""),
    }


@app.put("/api/llm/custom/{name}")
async def update_custom_model(name: str, req: _CustomUpdateReq):
    """编辑自定义模型。
    设计要点
    --------
    1. api_key 留空 = **保留原值**(防误清 — 这是用户改 key 时最常踩的坑)
    2. extra_headers **合并**而非替换(避免覆盖 group_id)
    3. group_id 显式空字符串 = 移除 MiniMax-Group-Id header;非空 = 设置 / 覆盖
    4. 改名时同步更新 ``.current_model``,避免选择状态指向不存在的名字
    """
    models = _load_custom()
    idx = next((i for i, m in enumerate(models) if m.get("name") == name), None)
    if idx is None:
        raise HTTPException(404, f"未找到: {name}")
    target = models[idx]

    # 改名冲突检测
    new_name = (req.name or "").strip() or target["name"]
    if new_name != name and any(m.get("name") == new_name for m in models):
        raise HTTPException(409, f"已存在同名模型: {new_name}")

    # 必填校验
    if req.base_url is not None and not req.base_url.strip():
        raise HTTPException(400, "base_url 不能为空")
    if req.model is not None and not req.model.strip():
        raise HTTPException(400, "model 不能为空")

    # 应用普通字段
    if req.base_url is not None:
        try:
            target["base_url"] = normalize_base_url(req.base_url)
        except ValueError as e:
            raise HTTPException(400, str(e))
    if req.model is not None:
        target["model"] = req.model.strip()
    if req.api_key is not None and req.api_key.strip():
        # 留空 = 保留原 key(防误清);非空 = 覆盖(按新名字存,改名时不会留下孤儿条目)
        target.update(_save_key(new_name, req.api_key))
    if req.note is not None:
        target["note"] = req.note

    # extra_headers + group_id 合并处理
    if req.extra_headers is not None or req.group_id is not None:
        extra = dict(target.get("extra_headers") or {})
        if req.extra_headers is not None:
            extra.update(req.extra_headers)
        if req.group_id is not None:
            gid = req.group_id.strip()
            if gid:
                extra["MiniMax-Group-Id"] = gid
            else:
                extra.pop("MiniMax-Group-Id", None)
        target["extra_headers"] = extra

    # 改名:同步 .current_model
    if new_name != name:
        # 凭据按模型名存放 → 不改名搬迁就再也 load 不到(key 静默丢失)。
        # 已换新 key 时新条目已在 new_name 下,这里只需清掉旧的。
        if req.api_key is not None and req.api_key.strip():
            _secrets.delete(name)
        else:
            _secrets.rename(name, new_name)
        target["name"] = new_name
        if CURRENT_MODEL_PATH.exists():
            try:
                cur = json.loads(CURRENT_MODEL_PATH.read_text(encoding="utf-8"))
                if cur.get("source") == "custom" and cur.get("name") == name:
                    cur["name"] = new_name
                    CURRENT_MODEL_PATH.write_text(
                        json.dumps(cur, ensure_ascii=False), encoding="utf-8"
                    )
            except Exception:
                pass

    target["updated"] = time.time()
    _save_custom(models)
    return {"ok": True, "name": target["name"]}


@app.delete("/api/llm/custom/{name}")
async def delete_custom_model(name: str):
    models = _load_custom()
    new_models = [m for m in models if m.get("name") != name]
    if len(new_models) == len(models):
        raise HTTPException(404, f"未找到: {name}")
    _save_custom(new_models)
    _secrets.delete(name)        # 别在 OS 凭据库里留孤儿条目
    # 如果当前选的就是它 → 清空选择
    if CURRENT_MODEL_PATH.exists():
        try:
            cur = json.loads(CURRENT_MODEL_PATH.read_text(encoding="utf-8"))
            if cur.get("source") == "custom" and cur.get("name") == name:
                CURRENT_MODEL_PATH.unlink()
        except Exception:
            pass
    return {"ok": True}


class _TestReq(BaseModel):
    source: str                  # "preset" | "custom"
    provider: Optional[str] = None
    name: Optional[str] = None

@app.post("/api/llm/test")
async def test_model(req: _TestReq):
    """连通性测试:发个 'hi' 看延迟/错误,不发实际聊天(避免消耗大量 token)。"""
    t0 = time.time()
    try:
        from openai import OpenAI
    except ImportError:
        raise HTTPException(500, "未安装 openai 库")
    client = None
    model = "default"
    if req.source == "preset":
        if not req.provider:
            raise HTTPException(400, "preset 必须给 provider")
        os_inst = _api.current_os()
        router = os_inst.router if os_inst is not None else None
        if router is None:
            raise HTTPException(503, "OS 未就绪")
        client, err = router._build_client(req.provider)
        if client is None:
            return {"ok": False, "error": err or "构造 client 失败", "latency_ms": 0}
        try:
            model = router._model_for(req.provider, "test", [{"role": "user", "content": "hi"}])
        except Exception:
            model = "default"
    elif req.source == "custom":
        models = _load_custom()
        match = next((m for m in models if m.get("name") == req.name), None)
        if not match:
            raise HTTPException(404, f"未找到自定义模型: {req.name}")
        key = _secrets.load(req.name or "", match.get("api_key_obf", ""))
        try:
            base_url = normalize_base_url(match.get("base_url", ""))
        except ValueError as e:
            return {"ok": False, "error": f"BASE URL 非法: {e}", "latency_ms": 0}
        try:
            client = OpenAI(
                api_key=key,
                base_url=base_url,
                timeout=15.0,
                default_headers=match.get("extra_headers") or {},
            )
        except Exception as e:
            return {"ok": False, "error": f"构造 client 失败: {e}", "latency_ms": 0}
        model = match["model"]
    else:
        raise HTTPException(400, f"未知 source: {req.source}")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
        )
        dt = int((time.time() - t0) * 1000)
        text = ""
        try:
            text = (resp.choices[0].message.content or "").strip()
        except Exception:
            pass
        return {"ok": True, "latency_ms": dt, "model": model, "reply": text}
    except Exception as e:
        dt = int((time.time() - t0) * 1000)
        return {"ok": False, "latency_ms": dt, "error": str(e)[:500]}


@app.get("/api/llm/runtime")
async def get_runtime():
    """当前真正生效的 provider/model(供前端高亮 + 调试)。"""
    os_inst = _api.current_os()
    router = os_inst.router if os_inst is not None else None
    if router is None:
        return {"ok": False, "error": "OS 未就绪"}
    # runtime 端点也走 lazy reload,让前端选完模型 → 立刻看到生效值
    # (complete() 入口也会调;这里额外调一次,GET 路径无副作用)
    try:
        router._maybe_reload_selection()
    except Exception:
        pass
    return {
        "ok": True,
        "provider": router.provider,
        "model":    getattr(router, "model", ""),
        "client_ok": getattr(router, "_client", None) is not None,
    }
