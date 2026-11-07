"""多模态对话:图片随问题发给视觉模型(model_router 多模态通道 + /chat/stream images)。
全离线:用假 client 捕获实际发出的 messages,验证按 provider 正确组装多模态 content。
模型无关:图片附到任意配置的模型,非视觉模型由 router 报错降级(不在此断言真实视觉)。"""
import os, sys, types, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest

def test_multimodal():
    # ISOLATION_GUARD:本用例依赖 import 时固化的模块级单例(网关/限流器/路由配置按 env 在 import 时定型),单进程全量跑会被前序用例污染 →
    # 假红。全量请用 tests/run_all.py(每文件独立子进程)。
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

    # [1] anthropic 组装:image/base64 block
    a = ModelRouter._attach_images("anthropic", BASE, IMG)
    blocks = a[-1]["content"]
    assert isinstance(blocks, list) and blocks[0] == {"type": "text", "text": "这是什么?"}, blocks
    assert blocks[1]["type"] == "image" and blocks[1]["source"]["type"] == "base64" \
        and blocks[1]["source"]["media_type"] == "image/png" and blocks[1]["source"]["data"] == "AAAABBBB", blocks
    assert BASE[-1]["content"] == "这是什么?", "不得改动原 messages(tier/stub/meter 仍需文本)"
    print("[1] anthropic 多模态 block 组装 OK")

    # [2] openai 系组装:image_url data URI
    for prov in ("openai", "deepseek", "qwen"):
        o = ModelRouter._attach_images(prov, BASE, IMG)
        blk = o[-1]["content"]
        assert blk[1]["type"] == "image_url" and blk[1]["image_url"]["url"] == "data:image/png;base64,AAAABBBB", (prov, blk)
    print("[2] openai/deepseek/qwen 多模态 image_url 组装 OK")

    # [3] google/未知 provider:无视觉通道 → 原样(纯文本降级)
    assert ModelRouter._attach_images("google", BASE, IMG) == BASE
    assert ModelRouter._attach_images("whatever", BASE, IMG) == BASE
    print("[3] google/未知 provider 纯文本降级 OK")

    # [4] 无 user 消息 / 已是多模态 → 不重复组装
    assert ModelRouter._attach_images("anthropic", [{"role": "assistant", "content": "x"}], IMG)[0]["content"] == "x"
    already = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert ModelRouter._attach_images("anthropic", already, IMG) == already
    assert ModelRouter._attach_images("anthropic", BASE, None) == BASE
    print("[4] 无 user / 已多模态 / 无图 边界 OK")

    # [5] _sanitize_images 护栏(数量/大小/类型/非列表)
    from foxclaw.api import _sanitize_images, _MAX_IMAGES, _MAX_IMG_B64
    big = "x" * (_MAX_IMG_B64 + 1)
    many = [{"media_type": "image/png", "data": "a"}] * (_MAX_IMAGES + 3)
    assert len(_sanitize_images(many)) == _MAX_IMAGES, "未限数量"
    assert _sanitize_images([{"media_type": "image/png", "data": big}]) == [], "未限大小"
    assert _sanitize_images([{"media_type": "image/tiff", "data": "a"}]) == [], "未按类型白名单"
    assert _sanitize_images([{"media_type": "image/jpeg", "data": ""}]) == [], "空数据未拒"
    assert _sanitize_images("notalist") == [] and _sanitize_images([1, "x"]) == []
    ok = _sanitize_images([{"media_type": "image/JPEG", "data": "abc"}])
    assert ok == [{"media_type": "image/jpeg", "data": "abc"}], ok    # 类型小写归一
    # 累计总量上限:每张 8MB,第 3 张会使总量 24MB>20MB → 只留前 2 张
    half = "x" * (8 * 1024 * 1024)
    three = [{"media_type": "image/png", "data": half}] * 3
    assert len(_sanitize_images(three)) == 2, "未按累计总量封顶"
    print("[5] _sanitize_images 护栏(数量/单图/累计总量/类型/归一)OK")

    # [6] _call_provider 把图片真正带进发给 provider 的 messages(openai 假 client 捕获)
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
    sent = cap["messages"][-1]["content"]      # 最后一条(user)应为多模态列表
    assert isinstance(sent, list) and any(b.get("type") == "image_url" for b in sent), sent
    print("[6] _call_provider 把图片带进 provider 请求 OK")

    # [7] router.stream(openai) 流式路径也带图(假流式 client 捕获)
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
    r2.provider = "openai"; r2._client = _make_openai_stream()   # 非 None → is_stub 自动为 False
    r2._locked_model = "gpt-4o"
    got = "".join(r2.stream("sys", BASE, images=IMG))
    assert got == "hi", got
    sSent = capS["messages"][-1]["content"]
    assert isinstance(sSent, list) and any(b.get("type") == "image_url" for b in sSent), sSent
    print("[7] router.stream 流式多模态带图 OK")

    # [8] stub 模式:带图不崩,忽略图片(无视觉时优雅降级)
    r3 = ModelRouter(provider="stub")
    assert r3.is_stub
    sc = r3.complete("sys", BASE, images=IMG)
    assert isinstance(sc, str) and sc, "stub complete 带图应正常返回文本"
    ss = "".join(r3.stream("sys", BASE, images=IMG))
    assert isinstance(ss, str) and ss, "stub stream 带图应正常返回文本"
    print("[8] stub 带图优雅降级(忽略图片,不崩)OK")

    # [9] /api/chat/stream 端到端:带图 SSE 正常产出(stub),超大图被 sanitize 掉不崩
    from fastapi.testclient import TestClient
    from foxclaw.api import app
    with TestClient(app) as c:
        body = {"task": "看图说话", "agent_id": "supervisor",
                "images": [{"media_type": "image/png", "data": "iVBORw0KGgo="},
                           {"media_type": "image/tiff", "data": "bad"},          # 类型不合法→被剔
                           {"media_type": "image/png", "data": "y" * (_MAX_IMG_B64 + 1)}]}  # 超大→被剔
        r = c.post("/api/chat/stream", json=body)
        assert r.status_code == 200, r.status_code
        assert "data:" in r.text, r.text[:200]
        # 无 error 事件(sanitize 把坏图剔掉,剩 1 张合法图走 stub)
        assert '"error"' not in r.text, r.text[:300]
    print("[9] /api/chat/stream 带图端到端(含超大/非法图被剔)OK")

    print("\n多模态对话通道验证全部通过")

if __name__ == "__main__":
    test_multimodal()
