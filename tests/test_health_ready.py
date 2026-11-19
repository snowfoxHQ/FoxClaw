"""/health/ready 组件三态:对象存在 ≠ ok,stub 必须如实报告(修复方案批次一 T4)。

锁住三件事:
1. hmr 三态按后端自报的 backend 标识判定(MockHMR.backend="Mock"),不看类名;
2. stub 不触发顶层 degraded —— 顶层 ok 语义 = 服务可接收请求,非"全部真实可用";
3. down 触发 degraded;缺 backend 属性的真实后端不得误判为 stub。
"""
import asyncio
import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# 先于 foxclaw.api 导入设好环境(模块级单例在 import 时固化,与 test_metrics 同款)
os.environ.setdefault("FOXCLAW_EMBEDDING_MODEL", "foxclaw-nonexistent-fast")
os.environ["HKC_EMBEDDING"] = "stub"
os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_health_")


def _stub_os(hmr=None):
    """最小 OS 桩:健康探针只读这五个属性。router 给非 stub 值,避免干扰 hmr 断言。"""
    return types.SimpleNamespace(hmr=hmr, scheduler=object(),
                                 router=types.SimpleNamespace(is_stub=False),
                                 _hkc_service=None, mcp_manager=None)


def _ready(monkeypatch, os_stub) -> dict:
    from foxclaw.api.routes import system as sys_routes
    import foxclaw.api as _api_mod
    monkeypatch.setattr(_api_mod, "current_os", lambda: os_stub)
    return asyncio.run(sys_routes.health_ready())


def test_hmr_three_states(monkeypatch):
    real = types.SimpleNamespace(backend="HMR")
    mock = types.SimpleNamespace(backend="Mock")
    assert _ready(monkeypatch, _stub_os(hmr=real))["components"]["hmr"] == "ok"
    assert _ready(monkeypatch, _stub_os(hmr=mock))["components"]["hmr"] == "stub"
    assert _ready(monkeypatch, _stub_os(hmr=None))["components"]["hmr"] == "down"


def test_backend_missing_is_ok_not_stub(monkeypatch):
    """无 backend 属性的真实后端(旧版实现):按真实后端对待,不误判 stub。"""
    j = _ready(monkeypatch, _stub_os(hmr=types.SimpleNamespace()))
    assert j["components"]["hmr"] == "ok", j


def test_stub_does_not_degrade_top_level(monkeypatch):
    """stub 存在时顶层仍 ok —— readiness 语义:ok=服务可接收请求,非全部真实。"""
    j = _ready(monkeypatch, _stub_os(hmr=types.SimpleNamespace(backend="Mock")))
    assert j["status"] == "ok" and j["components"]["hmr"] == "stub", j


def test_down_triggers_degraded(monkeypatch):
    j = _ready(monkeypatch, _stub_os(hmr=None))
    assert j["status"] == "degraded" and j["components"]["hmr"] == "down", j


def test_version_present(monkeypatch):
    from foxclaw import __version__
    assert _ready(monkeypatch, _stub_os())["version"] == __version__
