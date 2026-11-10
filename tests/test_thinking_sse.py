"""/api/chat/stream 的命名 SSE 事件:`event: thinking` 与正文 delta 分离。

前端按事件名折叠展示推理;正文 delta 里不得出现推理文本(否则复制/落库会被污染)。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_chat_stream_emits_named_thinking_event():
    if "foxclaw.api" in sys.modules:
        import pytest
        pytest.skip("需独立进程运行(模块级单例已固化),请用 tests/run_all.py")
    os.environ.setdefault("FOXCLAW_DATA_DIR", tempfile.mkdtemp(prefix="foxclaw_think_"))

    from fastapi.testclient import TestClient
    from foxclaw.agents.base import CognitiveAgent
    from foxclaw.api import app

    def _fake_stream(self, task, stop_event=None, images=None, history=None,
                     attachments=None, on_thinking=None, on_evidence=None):
        if on_thinking is not None:
            on_thinking("先分析一下用户意图")
        yield "好的"

    original = CognitiveAgent.stream_reply
    CognitiveAgent.stream_reply = _fake_stream
    try:
        with TestClient(app) as c:
            resp = c.post("/api/chat/stream", json={"task": "hi", "agent_id": "supervisor"})
            assert resp.status_code == 200, resp.status_code
            body = resp.text
    finally:
        CognitiveAgent.stream_reply = original

    assert "event: thinking" in body, "推理必须以命名事件送出"
    blocks = [b for b in body.split("\n\n") if b.strip()]
    think_payloads, delta_payloads = [], []
    for b in blocks:
        name = "message"
        data = None
        for line in b.splitlines():
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                data = line[6:].strip()
        if data is None:
            continue
        ev = json.loads(data)
        if name == "thinking":
            think_payloads.append(ev)
        elif "delta" in ev:
            delta_payloads.append(ev["delta"])

    assert think_payloads, "应有 thinking 事件块"
    assert "先分析一下用户意图" == "".join(p.get("thinking", "") for p in think_payloads)
    joined = "".join(delta_payloads)
    assert joined == "好的"
    assert "先分析一下" not in joined, "推理不得混入正文 delta"
