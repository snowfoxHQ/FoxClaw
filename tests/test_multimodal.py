"""多模态对话:图片随问题发给视觉模型(model_router 多模态通道 + /chat/stream images)。
全离线:用假 client 捕获实际发出的 messages,验证按 provider 正确组装多模态 content。
模型无关:图片附到任意配置的模型,非视觉模型由 router 报错降级(不在此断言真实视觉)。"""
import os, sys, types, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest

def test_multimodal():
    if "foxclaw.core.model_router" in sys.modules:
        pytest.skip("需独立进程运行(模块级单例已固化),请用 tests/run_all.py")
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        os.environ.pop(k, None)
    os.environ["HKC_EMBEDDING"] = "stub"
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"
    os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_mm_")

    from foxclaw.core.model_router import ModelRouter

    print("=" * 60); print("多模态对话通道验证"); print("=" * 60)

    IMG = [{"media_type": "image/png", "data": "AAAABBBB"}]
    BASE = [{"role": "user", "content": "这是什么?"}]

    a = ModelRouter._attach_images("anthropic", BASE, IMG)
    blocks = a[-1]["content"]
    assert isinstance(blocks, list) and blocks[0] == {"type": "text", "text": "这是什么?"}, blocks
    assert blocks[1]["type"] == "image" and blocks[1]["source"]["type"] == "base64" \
        and blocks[1]["source"]["media_type"] == "image/png" and blocks[1]["source"]["data"] == "AAAABBBB", blocks
    assert BASE[-1]["content"] == "这是什么?", "不得改动原 messages(tier/stub/meter 仍需文本)"
    print("[1] anthropic 多模态 block 组装 OK")

    for prov in ("openai", "deepseek", "qwen"):
        o = ModelRouter._attach_images(prov, BASE, IMG)
        blk = o[-1]["content"]
        assert blk[1]["type"] == "image_url" and blk[1]["image_url"]["url"] == "data:image/png;base64,AAAABBBB", (prov, blk)
    print("[2] openai/deepseek/qwen 多模态 image_url 组装 OK")

    assert ModelRouter._attach_images("google", BASE, IMG) == BASE
    assert ModelRouter._attach_images("whatever", BASE, IMG) == BASE
    print("[3] google/未知 provider 纯文本降级 OK")

    assert ModelRouter._attach_images("anthropic", [{"role": "assistant", "content": "x"}], IMG)[0]["content"] == "x"
    already = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert ModelRouter._attach_images("anthropic", already, IMG) == already
    assert ModelRouter._attach_images("anthropic", BASE, None) == BASE
    print("[4] 无 user / 已多模态 / 无图 边界 OK")

    from foxclaw.api import _sanitize_images, _MAX_IMAGES, _MAX_IMG_B64
    big = "x" * (_MAX_IMG_B64 + 1)
    many = [{"media_type": "image/png", "data": "a"}] * (_MAX_IMAGES + 3)
    assert len(_sanitize_images(many)) == _MAX_IMAGES, "未限数量"
    assert _sanitize_images([{"media_type": "image/png", "data": big}]) == [], "未限大小"
    assert _sanitize_images([{"media_type": "image/tiff", "data": "a"}]) == [], "未按类型白名单"
    assert _sanitize_images([{"media_type": "image/jpeg", "data": ""}]) == [], "空数据未拒"
    assert _sanitize_images("notalist") == [] and _sanitize_images([1, "x"]) == []
    ok = _sanitize_images([{"media_type": "image/JPEG", "data": "abc"}])
    assert ok == [{"media_type": "image/jpeg", "data": "abc"}], ok
    half = "x" * (8 * 1024 * 1024)
    three = [{"media_type": "image/png", "data": half}] * 3
    assert len(_sanitize_images(three)) == 2, "未按累计总量封顶"
    print("[5] _sanitize_images 护栏(数量/单图/累计总量/类型/归一)OK")

    cap = {}
    def _make_openai():
        def create(model, max_tokens, messages, **kw):
            cap["messages"] = messages
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))],
                usage=None)
        return types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create)))
    r = ModelRouter()
    out, _ = r._call_provider("openai", _make_openai(), "gpt-4o", "sys", BASE, 100, images=IMG)
    assert out == "ok"
    sent = cap["messages"][-1]["content"]
    assert isinstance(sent, list) and any(b.get("type") == "image_url" for b in sent), sent
    print("[6] _call_provider 把图片带进 provider 请求 OK")

    capS = {}
    def _make_openai_stream():
        def create(model, max_tokens, messages, stream=False, **kw):
            capS["messages"] = messages
            chunk = types.SimpleNamespace(choices=[types.SimpleNamespace(
                delta=types.SimpleNamespace(content="hi"))])
            return iter([chunk])
        return types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create)))
    r2 = ModelRouter()
    r2.provider = "openai"; r2._client = _make_openai_stream()
    r2._locked_model = "gpt-4o"
    got = "".join(r2.stream("sys", BASE, images=IMG))
    assert got == "hi", got
    sSent = capS["messages"][-1]["content"]
    assert isinstance(sSent, list) and any(b.get("type") == "image_url" for b in sSent), sSent
    print("[7] router.stream 流式多模态带图 OK")

    r3 = ModelRouter(provider="stub")
    assert r3.is_stub
    sc = r3.complete("sys", BASE, images=IMG)
    assert isinstance(sc, str) and sc, "stub complete 带图应正常返回文本"
    ss = "".join(r3.stream("sys", BASE, images=IMG))
    assert isinstance(ss, str) and ss, "stub stream 带图应正常返回文本"
    print("[8] stub 带图优雅降级(忽略图片,不崩)OK")

    from fastapi.testclient import TestClient
    from foxclaw.api import app
    with TestClient(app) as c:
        body = {"task": "看图说话", "agent_id": "supervisor",
                "images": [{"media_type": "image/png", "data": "iVBORw0KGgo="},
                           {"media_type": "image/tiff", "data": "bad"},
                           {"media_type": "image/png", "data": "y" * (_MAX_IMG_B64 + 1)}]}
        r = c.post("/api/chat/stream", json=body)
        assert r.status_code == 200, r.status_code
        assert "data:" in r.text, r.text[:200]
        assert '"error"' not in r.text, r.text[:300]
    print("[9] /api/chat/stream 带图端到端(含超大/非法图被剔)OK")

    print("\n多模态对话通道验证全部通过")

if __name__ == "__main__":
    test_multimodal()
