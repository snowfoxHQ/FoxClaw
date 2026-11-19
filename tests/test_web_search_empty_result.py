"""web_search 空结果证据链测试（复核者裁定 2026-11-16，定点扩展）。

三态边界（不得混淆）：
- 搜索成功有结果 → SUCCESS / NON_EMPTY 落盘；
- 搜索服务成功完成查询且明确零命中 → SUCCESS / EMPTY 落盘；
- 端点失败（网络/HTTP/权限/解析/结构损坏）→ FAILED，绝不生成成功步骤。
判断只依据端点响应结构，绝不依据 Agent 回复或输出格式。
"""
import io
import json
import urllib.error
from types import SimpleNamespace

from foxclaw.core import web_search as _ws
from foxclaw.core import tools as _tools
from foxclaw.agents.mixins import tool_exec as _te

EMPTY = _tools.EMPTY_RESULT_MARKER


class _Resp:
    def __init__(self, payload):
        self._raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http(code):
    return urllib.error.HTTPError("https://example.test", code, "err", {},
                                  io.BytesIO(b'{"error":"denied"}'))


def _patch(monkeypatch, *responses):
    seq = list(responses)

    def fake_urlopen(req, timeout=None):
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(_ws.urllib.request, "urlopen", fake_urlopen)
    return seq


def _agent(rec=None):
    return SimpleNamespace(AGENT_ID="office", _session_id="sess_w",
                           _recovery=rec, _think=lambda **kw: None)


class _Rec:
    def __init__(self):
        self.steps = []

    def record_step(self, tid, step):
        self.steps.append(dict(step))


def _call(name="web_search", args=None):
    return {"name": name, "args": args or {"query": "HTTP 缩写"}}


# ---------- 三态分类 ----------

def test_hit_success(monkeypatch):
    """场景1：web_search 正常命中 → SUCCESS + 结果保留。"""
    _patch(monkeypatch, _Resp({"choices": [{"message": {"content": "答案"}}],
                               "references": [{"title": "t", "url": "https://a.test"}]}))
    out = _ws._search_answer_inner("q", "k")
    assert out["status"] == _ws.SEARCH_SUCCESS
    assert out["references"][0]["url"] == "https://a.test"


def test_zero_hit_empty(monkeypatch):
    """场景2：纯检索端点 HTTP 200 + references=[] → 明确零命中 EMPTY。"""
    _patch(monkeypatch, _Resp({"references": []}))
    out = _ws._search_refs_only("q", "k")
    assert out["status"] == _ws.SEARCH_EMPTY
    assert out["references"] == []


def test_service_error_response_failed(monkeypatch):
    """场景3：服务返回错误 JSON（无 references/choices 结构）→ FAILED。"""
    _patch(monkeypatch, _Resp({"error": "bad request"}), _Resp({"error": "bad request"}))
    assert _ws._search_answer_inner("q", "k")["status"] == _ws.SEARCH_FAILED


def test_network_exception_failed(monkeypatch):
    """场景4：网络异常/超时 → FAILED。"""
    _patch(monkeypatch, TimeoutError("timeout"), TimeoutError("timeout"))
    assert _ws._search_answer_inner("q", "k")["status"] == _ws.SEARCH_FAILED


def test_permission_denied_failed(monkeypatch):
    """场景5：权限拒绝（两个端点都 403）→ FAILED。"""
    _patch(monkeypatch, _http(403), _http(403))
    assert _ws._search_answer_inner("q", "k")["status"] == _ws.SEARCH_FAILED


def test_broken_structure_failed(monkeypatch):
    """场景6：响应结构损坏（references 非列表）→ FAILED，不判零命中。"""
    _patch(monkeypatch, _Resp({"references": "not-a-list"}))
    assert _ws._search_refs_only("q", "k")["status"] == _ws.SEARCH_FAILED


def test_no_key_failed(monkeypatch):
    _patch(monkeypatch)  # 不应发起任何网络请求
    monkeypatch.setattr(_ws, "_key", lambda: "")
    assert _ws.search_answer("q")["status"] == _ws.SEARCH_FAILED


# ---------- 回退路径 ----------

def test_llm_fail_pure_search_zero_hit_empty(monkeypatch):
    """场景8：LLM 端点失败 → 纯检索回退成功零命中 → EMPTY。"""
    _patch(monkeypatch, _http(401), _Resp({"references": []}))
    out = _ws._search_answer_inner("q", "k")
    assert out["status"] == _ws.SEARCH_EMPTY


def test_both_endpoints_fail_failed(monkeypatch):
    """场景9：LLM 端点失败 + 纯检索也失败 → FAILED。"""
    _patch(monkeypatch, _http(401), _http(503))
    assert _ws._search_answer_inner("q", "k")["status"] == _ws.SEARCH_FAILED


# ---------- 证据落盘 ----------

def test_dispatch_hit_then_record_non_empty(monkeypatch):
    """命中 → 工具文本正常 → NON_EMPTY 步骤落盘（T6-001 无回归）。"""
    monkeypatch.setattr(_ws, "search_answer", lambda q: {
        "status": _ws.SEARCH_SUCCESS, "answer": "结果",
        "references": [{"title": "t", "url": "https://a.test"}]})
    rec = _Rec()
    result = _te._invoke_tool(_agent(rec), "web_search", _call()["args"])
    _te._settle(_agent(rec), _call(), result)
    steps = [s for s in rec.steps if s.get("kind") == "tool"]
    assert len(steps) == 1 and steps[0]["result_status"] == "NON_EMPTY"
    assert "https://a.test" in steps[0]["result"]


def test_dispatch_empty_then_record_empty(monkeypatch):
    """零命中 → EMPTY 标记 → result_status=EMPTY 步骤落盘（T6-008 目标）。"""
    monkeypatch.setattr(_ws, "search_answer", lambda q: {
        "status": _ws.SEARCH_EMPTY, "answer": "", "references": []})
    rec = _Rec()
    result = _te._invoke_tool(_agent(rec), "web_search", _call()["args"])
    assert result == EMPTY
    _te._settle(_agent(rec), _call(), result)
    steps = [s for s in rec.steps if s.get("kind") == "tool"]
    assert len(steps) == 1 and steps[0]["result_status"] == "EMPTY"
    assert steps[0]["name"] == "web_search" and steps[0]["args"] == _call()["args"]


def test_legacy_shape_with_content_ok_but_empty_never_becomes_empty(monkeypatch):
    """旧格式兼容:有内容仍成功;旧空格式不得误判 EMPTY(裁定边界)。"""
    monkeypatch.setattr(_ws, "search_answer", lambda q: {"answer": "XYZ", "references": []})
    out = _te._invoke_tool(_agent(), "web_search", _call()["args"])
    assert "XYZ" in out and out != EMPTY
    monkeypatch.setattr(_ws, "search_answer", lambda q: {})
    out2 = _te._invoke_tool(_agent(), "web_search", _call()["args"])
    assert out2.startswith("(") and out2 != EMPTY


def test_agent_claim_without_execution_no_success_evidence(monkeypatch):
    """场景7：搜索失败时 Agent 即使自述为空，也不生成成功证据。"""
    monkeypatch.setattr(_ws, "search_answer", lambda q: {
        "status": _ws.SEARCH_FAILED, "answer": "", "references": []})
    rec = _Rec()
    result = _te._invoke_tool(_agent(rec), "web_search", _call()["args"])
    assert result != EMPTY and result.startswith("(")
    _te._settle(_agent(rec), _call(), result)
    assert [s for s in rec.steps if s.get("kind") == "tool"] == []
