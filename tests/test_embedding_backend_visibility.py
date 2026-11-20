"""embedding 后端降级必须可观测。

背景:`HKC_EMBEDDING=auto`(默认)下本地模型加载失败,`make_backend` 会静默返回
`StubBackend` —— 它用文本 hash 生成确定性伪向量,能建索引、能检索、分数还很好看,
但和语义完全无关。若只在日志里留一条 warning,HKC 依然报 ok,用户看到的是
"知识库建好了、能问答了",实际是随机命中。这与 P0-03 的假成功同类,只是发生在
向量层,所以要把"当前后端是真语义还是伪向量"变成可查询状态。

区分两种 stub:
  · auto/local 下意外退化 → degraded=True(语义检索事实上不可用);
  · 用户显式 HKC_EMBEDDING=stub → degraded=False(已知取舍,不该报故障)。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.knowledge import HKCService


class _ContainerStub:
    """只提供 embedding_info 依赖的字段。"""

    def __init__(self, backend_name: str):
        self._embedding_name = backend_name


def _svc(backend_name: str) -> HKCService:
    return HKCService(_ContainerStub(backend_name), client=object())


def test_local_backend_is_semantic(monkeypatch):
    monkeypatch.delenv("HKC_EMBEDDING", raising=False)
    info = _svc("LocalST(BAAI/bge-m3,1024d)").embedding_info
    assert info["semantic"] is True
    assert info["degraded"] is False
    assert "bge-m3" in info["backend"]


def test_auto_fallback_to_stub_is_degraded(monkeypatch):
    """默认 auto 下退化成伪向量 = 语义检索不可用,必须标记出来。"""
    monkeypatch.delenv("HKC_EMBEDDING", raising=False)
    info = _svc("Stub(dim=384)").embedding_info
    assert info["semantic"] is False
    assert info["degraded"] is True
    assert info["configured"] == "auto"


def test_local_configured_but_stub_running_is_degraded(monkeypatch):
    """显式要 local 却跑成 stub:更明确的事故,同样算 degraded。"""
    monkeypatch.setenv("HKC_EMBEDDING", "local")
    info = _svc("Stub(dim=384)").embedding_info
    assert info["degraded"] is True


def test_explicit_stub_is_not_degraded(monkeypatch):
    """用户主动关掉语义检索,是已知取舍,不该报警。"""
    monkeypatch.setenv("HKC_EMBEDDING", "stub")
    info = _svc("Stub(dim=384)").embedding_info
    assert info["semantic"] is False
    assert info["degraded"] is False


def test_empty_backend_name_is_not_semantic(monkeypatch):
    """拿不到后端名时不能默认当成可用。"""
    monkeypatch.delenv("HKC_EMBEDDING", raising=False)
    info = _svc("").embedding_info
    assert info["semantic"] is False
    assert info["backend"] == ""
    assert info["degraded"] is False
