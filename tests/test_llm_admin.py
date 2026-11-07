"""LLM 模型管理「自定义模型填不上」回归测试。

覆盖三条曾经真实踩过的坑:
1. BASE URL 没写 /v1 → SDK 拼出 /chat/completions → 网关 404/401(用户以为"填不上")
2. 保存了自定义模型但**没切换** → 聊天仍走 .env 旧 provider → AuthenticationError + stub
3. provider="custom" 不在 ``_OPENAI_FAMILY`` → stream() 退化为一次性输出、工具能力静默关闭

全离线:不联网、不加载嵌入模型(provider=stub 起 router)。
"""
import os, sys, json, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 环境隔离:必须在 import foxclaw 之前 ──────────────────────────────
for _k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GOOGLE_API_KEY",
           "DASHSCOPE_API_KEY", "MINIMAX_API_KEY", "LLM_API_KEY", "FOXCLAW_API_KEY"):
    os.environ.pop(_k, None)
os.environ["HKC_EMBEDDING"] = "stub"
os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_llmadmin_")
os.environ["FOXCLAW_SKIP_DOTENV"] = "1"
os.environ["FOXCLAW_PROVIDER"] = ""
os.environ["FOXCLAW_MODEL"] = ""
os.environ["FOXCLAW_MCP_SERVER"] = "0"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import pytest
from fastapi import HTTPException

from foxclaw.core.model_router import ModelRouter, normalize_base_url
import foxclaw.api as _api
from foxclaw.api.routes import llm_admin


# ── 1. BASE URL 规范化 ───────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("https://api.deepseek.com",            "https://api.deepseek.com/v1"),
    ("https://api.deepseek.com/",           "https://api.deepseek.com/v1"),
    ("https://api.deepseek.com/v1",         "https://api.deepseek.com/v1"),
    ("https://api.deepseek.com/v1/",        "https://api.deepseek.com/v1"),
    ("  https://api.openai.com/v1  ",       "https://api.openai.com/v1"),
    ("api.deepseek.com",                    "https://api.deepseek.com/v1"),   # 漏 scheme
    ("api.deepseek.com/v1",                 "https://api.deepseek.com/v1"),
    # 已有非 /v1 的 path 必须原样保留(通义兼容模式 / 自建网关 /openai/v1)
    ("https://dashscope.aliyuncs.com/compatible-mode/v1",
     "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ("http://localhost:11434/v1",           "http://localhost:11434/v1"),
    ("http://127.0.0.1:8080/openai/v1",     "http://127.0.0.1:8080/openai/v1"),
])
def test_normalize_base_url(raw, expected):
    assert normalize_base_url(raw) == expected
    # 幂等:规范化两次结果不变(前端 blur + 后端保存 + router 构造都会过一遍)
    assert normalize_base_url(expected) == expected


@pytest.mark.parametrize("bad", ["", "   ", "ftp://example.com", "https://"])
def test_normalize_base_url_rejects(bad):
    with pytest.raises(ValueError):
        normalize_base_url(bad)


# ── 2. custom 必须被当作 openai-compatible 家族成员 ──────────────────

def test_custom_is_openai_family():
    assert "custom" in ModelRouter._OPENAI_FAMILY, \
        "custom 漏了会让流式退化为一次性输出、工具能力静默关闭"
    assert "custom" in ModelRouter._TOOL_PROVIDERS


def test_custom_provider_supports_tools():
    r = ModelRouter(provider="stub")
    r.provider = "custom"
    r._client = object()          # 假装已构造好(不联网)
    assert r.supports_tools() is True


# ── 3. stream() 必须 reload 选择(聊天主路径)─────────────────────────

def test_stream_reloads_selection():
    r = ModelRouter(provider="stub")
    hits = []
    r._maybe_reload_selection = lambda: hits.append(True)   # 不打真实文件
    list(r.stream("sys", [{"role": "user", "content": "hi"}]))
    assert hits, "stream() 未调用 _maybe_reload_selection → 切模型后流式对话不生效"


# ── 4. 保存 / 切换端点 ───────────────────────────────────────────────

@pytest.fixture()
def fresh_store(monkeypatch):
    """每个用例一份干净的 custom_models.json + .current_model。"""
    d = Path(tempfile.mkdtemp(prefix="llm_admin_store_"))
    # router 侧按 env 现算路径(_custom_models_path / _selection_path),必须一起指过来,
    # 否则端点写 A 目录、router 读 B 目录 → 预检假失败。
    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(d))
    monkeypatch.setattr(llm_admin, "CUSTOM_MODELS_PATH", d / "custom_models.json")
    monkeypatch.setattr(llm_admin, "CURRENT_MODEL_PATH", d / ".current_model")
    yield d


def _add(name="deepseek", base="https://api.deepseek.com", key="sk-test", model="deepseek-chat"):
    import asyncio
    return asyncio.run(llm_admin.add_custom_model(
        llm_admin._CustomAddReq(name=name, base_url=base, api_key=key, model=model)))


def test_add_normalizes_base_url(fresh_store):
    _add(base="https://api.deepseek.com")
    saved = json.loads((fresh_store / "custom_models.json").read_text(encoding="utf-8"))
    assert saved[0]["base_url"] == "https://api.deepseek.com/v1"

    # 更新时同样规范化
    import asyncio
    asyncio.run(llm_admin.update_custom_model("deepseek", llm_admin._CustomUpdateReq(
        base_url="https://api.deepseek.com")))
    saved = json.loads((fresh_store / "custom_models.json").read_text(encoding="utf-8"))
    assert saved[0]["base_url"] == "https://api.deepseek.com/v1"


def test_add_rejects_bad_base_url(fresh_store):
    with pytest.raises(HTTPException) as ei:
        _add(base="ftp://example.com")
    assert ei.value.status_code == 400


def test_select_custom_takes_effect_immediately(fresh_store, monkeypatch):
    """核心回归:保存后 point 到它必须真的把 router 切过去。"""
    _add()
    router = ModelRouter(provider="stub")

    class _FakeOS:
        pass
    fake = _FakeOS()
    fake.router = router
    monkeypatch.setattr(_api, "_os_instance", fake, raising=False)

    import asyncio
    out = asyncio.run(llm_admin.select_model(
        llm_admin._SelectReq(source="custom", name="deepseek")))

    assert out["current"]["name"] == "deepseek"
    assert out["runtime"]["provider"] == "custom"
    assert out["runtime"]["model"] == "deepseek-chat"
    assert router.provider == "custom" and router.model == "deepseek-chat"
    # .current_model 落盘,重启后 lazy reload 仍能读到
    cur = json.loads((fresh_store / ".current_model").read_text(encoding="utf-8"))
    assert cur["source"] == "custom" and cur["name"] == "deepseek"


def test_select_missing_custom_404(fresh_store):
    import asyncio
    with pytest.raises(HTTPException) as ei:
        asyncio.run(llm_admin.select_model(
            llm_admin._SelectReq(source="custom", name="nope")))
    assert ei.value.status_code == 404


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
