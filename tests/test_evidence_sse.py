"""/api/chat/stream 的命名 SSE 事件:`event: evidence`(执行凭据)与正文 delta 分离。

锁住的契约:
  · 工具轮每步的核验结论 + 凭据以**命名事件**整块送出,不拼进正文(否则复制/落库被污染);
  · 没有工具步的纯对话不产生 evidence 事件(不能凭空生成"凭据",前端也不闪空面板);
  · 前端拿到的是一个数组:`[{tool,status,reason,evidence:[{kind,ref,label,...}]}]`。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

EVIDENCE = [
    {"tool": "web_search", "kind": "ok", "status": "verified", "reason": "",
     "evidence": [{"kind": "url", "ref": "https://example.com/a", "label": "example.com",
                   "source": "web_search", "at": 0}]},
    {"tool": "send_mail", "kind": "ok", "status": "unverified",
     "reason": "该工具未声明可观察的后置条件", "evidence": []},
]


def _parse(body: str) -> tuple[list, str]:
    """拆 SSE:返回 (evidence 事件载荷列表, 正文 delta 拼接)。"""
    ev_payloads, delta_text = [], ""
    for b in [x for x in body.split("\n\n") if x.strip()]:
        name, data = "message", None
        for line in b.splitlines():
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                data = line[6:].strip()
        if data is None:
            continue
        ev = json.loads(data)
        if name == "evidence":
            ev_payloads.append(ev)
        elif "delta" in ev:
            delta_text += ev["delta"]
    return ev_payloads, delta_text


def test_chat_stream_evidence_event_contract():
    if "foxclaw.api" in sys.modules:
        import pytest
        pytest.skip("需独立进程运行(模块级单例已固化),请用 tests/run_all.py")
    os.environ.setdefault("FOXCLAW_DATA_DIR", tempfile.mkdtemp(prefix="foxclaw_ev_"))

    from fastapi.testclient import TestClient
    from foxclaw.agents.base import CognitiveAgent
    from foxclaw.api import app

    seen: dict = {}

    def _fake_stream(self, task, stop_event=None, images=None, history=None,
                     attachments=None, on_thinking=None, on_evidence=None):
        seen["has_cb"] = on_evidence is not None
        if task == "plain":
            yield "纯对话,没有动用工具。"
            return
        yield "已完成。"
        if on_evidence is not None:
            on_evidence(EVIDENCE)

    original = CognitiveAgent.stream_reply
    CognitiveAgent.stream_reply = _fake_stream
    try:
        with TestClient(app) as c:
            with_tools = c.post("/api/chat/stream", json={"task": "use-tools", "agent_id": "supervisor"})
            plain = c.post("/api/chat/stream", json={"task": "plain", "agent_id": "supervisor"})
            assert with_tools.status_code == 200 and plain.status_code == 200
            body, plain_body = with_tools.text, plain.text
    finally:
        CognitiveAgent.stream_reply = original

    assert seen.get("has_cb") is True, "端点必须把 on_evidence 传给 Agent"

    ev_payloads, delta_text = _parse(body)
    assert "event: evidence" in body, "证据必须以命名事件送出"
    assert len(ev_payloads) == 1, "一轮只上报一次"
    items = ev_payloads[0]["evidence"]
    assert [i["tool"] for i in items] == ["web_search", "send_mail"]
    assert [i["status"] for i in items] == ["verified", "unverified"]
    assert items[0]["evidence"][0]["ref"] == "https://example.com/a"
    assert "example.com" not in delta_text
    assert delta_text.strip() == "已完成。"

    assert "event: evidence" not in plain_body
    assert "纯对话" in plain_body
