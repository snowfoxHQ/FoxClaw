"""聊天附件(多模态渲染·步骤7)回归测试:文件上传/下载、正文提取、护栏、LLM 上下文注入、会话落盘。

全离线:锁 stub 模型 + stub 嵌入 + 临时数据目录,不碰真实 API / 不加载真实向量模型。
与 test_multimodal.py(图片通道)互补:这里覆盖「文件」通道(图片走 images base64,不经过附件)。
"""
import os, sys, tempfile, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

for _k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
           "GOOGLE_API_KEY", "DASHSCOPE_API_KEY", "FOXCLAW_API_KEY"):
    os.environ.pop(_k, None)
os.environ["HKC_EMBEDDING"] = "stub"
os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"
os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_att_")
os.environ["FOXCLAW_SKIP_DOTENV"] = "1"
os.environ["FOXCLAW_PROVIDER"] = ""
os.environ["FOXCLAW_MODEL"] = ""
os.environ["FOXCLAW_MCP_SERVER"] = "0"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import pytest
from fastapi.testclient import TestClient

from foxclaw.api import app, _sanitize_attachments
from foxclaw.api.routes.task import _MAX_ATTACHMENTS, _ATTACH_TEXT_MAX, _ATTACH_TEXT_TOTAL
from foxclaw.agents.base import CognitiveAgent
from foxclaw.core.sessions import SessionStore


@pytest.fixture()
def store():
    d = tempfile.mkdtemp(prefix="att_sess_")
    try:
        yield SessionStore(Path(d) / "sessions")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_sanitize_attachments():
    many = [{"name": f"f{i}.txt", "text": "t"} for i in range(_MAX_ATTACHMENTS + 4)]
    assert len(_sanitize_attachments(many)) == _MAX_ATTACHMENTS

    one = _sanitize_attachments([{"name": "a.txt", "text": "x" * (_ATTACH_TEXT_MAX + 100)}])
    assert len(one[0]["text"]) == _ATTACH_TEXT_MAX

    assert _sanitize_attachments([{"name": "a" * 300, "text": "x"}])[0]["name"] == "a" * 200

    big = "x" * (_ATTACH_TEXT_MAX + 1000)
    four = _sanitize_attachments([{"name": f"f{i}.txt", "text": big} for i in range(4)])
    assert len(four) == 4
    assert sum(len(a["text"]) for a in four) == _ATTACH_TEXT_TOTAL
    assert four[3]["text"] == ""

    assert _sanitize_attachments("notalist") == []
    assert _sanitize_attachments([1, "x"]) == []
    assert _sanitize_attachments([{"name": "", "text": ""}]) == []
    assert _sanitize_attachments(None) == []

    out = _sanitize_attachments([{"name": "a", "size": "big", "mime": None, "url": None, "text": "t"}])
    assert out[0]["size"] == 0 and out[0]["mime"] == "" and out[0]["url"] == ""
    print("护栏(数量/单文件/累计/类型清洗/边界)OK")


def test_attachments_context():
    atts = [
        {"name": "a.txt", "size": 11, "mime": "text/plain", "url": "", "text": "hello world"},
        {"name": "b.pdf", "size": 999, "mime": "application/pdf", "url": "", "text": ""},
    ]
    ctx = CognitiveAgent._attachments_context(atts)
    assert "【附件「a.txt」内容如下】" in ctx and "hello world" in ctx
    assert "【附件「b.txt」" not in ctx
    assert "b.pdf" in ctx and "无法提取" in ctx
    assert CognitiveAgent._attachments_context([]) == ""
    assert CognitiveAgent._attachments_context(None) == ""
    assert CognitiveAgent._attachments_context([1, "x"]) == ""
    print("LLM 上下文注入(正文/降级/空)OK")


def test_sessions_append_attachments(store):
    s = store.create()
    atts = [
        {"name": "a.txt", "size": 11, "mime": "text/plain", "url": "/api/attachments/abc", "text": "正文不落盘"},
        {"name": "b.pdf", "size": 999, "mime": "application/pdf", "url": "/api/attachments/def", "text": ""},
    ]
    assert store.append(s["id"], "user", "读一下这两个文件", attachments=atts)
    d = store.get(s["id"])
    m = d["messages"][0]
    assert m["role"] == "user" and m["content"] == "读一下这两个文件"
    assert len(m["attachments"]) == 2
    assert m["attachments"][0] == {"name": "a.txt", "size": 11, "mime": "text/plain", "url": "/api/attachments/abc"}
    assert "text" not in m["attachments"][0], "text 不得落盘(会话文件不膨胀)"
    assert store.append(s["id"], "assistant", "好")
    assert "attachments" not in store.get(s["id"])["messages"][1]
    print("会话落盘(轻字段/不存 text/向后兼容)OK")


def test_upload_and_download():
    with TestClient(app) as c:
        r = c.post("/api/upload", files={"file": ("笔记.txt", "今天学了三件事".encode(), "text/plain")})
        assert r.status_code == 200, r.text
        a = r.json()
        assert a["name"] == "笔记.txt" and a["text"] == "今天学了三件事"
        assert a["url"].startswith("/api/attachments/") and a["id"] and a["size"] > 0

        r2 = c.get(a["url"])
        assert r2.status_code == 200
        assert r2.content.decode("utf-8") == "今天学了三件事"

        r3 = c.post("/api/upload", files={"file": ("img.bin", b"\x00\x01\x02", "application/octet-stream")})
        assert r3.status_code == 200 and r3.json()["text"] == ""

        assert c.post("/api/upload", files={"file": ("e.txt", b"", "text/plain")}).status_code == 400
        big = b"x" * (21 * 1024 * 1024)
        assert c.post("/api/upload", files={"file": ("big.txt", big, "text/plain")}).status_code == 413

        assert c.get("/api/attachments/../../etc/passwd").status_code == 404
        assert c.get("/api/attachments/a.b").status_code == 400
        assert c.get("/api/attachments/deadbeef0000").status_code == 404
    print("上传/下载(正文提取/二进制降级/空/超大/穿越/404)OK")


def test_chat_stream_with_attachments():
    import foxclaw.api as _api
    with TestClient(app) as c:
        up = c.post("/api/upload", files={"file": ("要点.txt", "预算 100 万".encode(), "text/plain")})
        att = up.json()
        sid = _api.os_instance().sessions.create()["id"]
        payload = {
            "task": "帮我总结要点",
            "agent_id": "supervisor",
            "session_id": sid,
            "attachments": [{"name": att["name"], "size": att["size"], "mime": att["mime"],
                             "url": att["url"], "text": att["text"]}],
        }
        r = c.post("/api/chat/stream", json=payload)
        assert r.status_code == 200, r.text[:200]
        assert "data:" in r.text and '"error"' not in r.text, r.text[:300]

        d = _api.os_instance().sessions.get(sid)
        assert d is not None, "会话未落盘"
        um = d["messages"][0]
        assert um["role"] == "user" and um["content"] == "帮我总结要点"
        assert um["attachments"][0]["name"] == "要点.txt"
        assert "text" not in um["attachments"][0]
    print("/chat/stream 带附件端到端(SSE + 会话落盘)OK")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
