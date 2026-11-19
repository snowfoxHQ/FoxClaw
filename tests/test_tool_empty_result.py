"""只读工具空结果证据链测试（复核者裁定 2026-11-16，裁定 A）。

分离「执行状态」与「业务结果」两个维度：
- read_only 工具成功执行但无命中 = EMPTY（执行 SUCCESS），必须落盘可追溯证据；
- 异常/超时/参数错/权限拒绝 = FAILED，绝不落成功证据；
- 非只读工具绝不允许套用 OK-empty（verify 拒绝 + _settle 防御双保险）；
- EMPTY 标记按模块常量等值判定，不靠任意字符串。
"""
from types import SimpleNamespace

from foxclaw.core import tools as _tools
from foxclaw.core import verify as _verify
from foxclaw.agents.mixins import tool_exec as _te

EMPTY = _tools.EMPTY_RESULT_MARKER
READ_ONLY_CAP = _tools.tool_capability("recall_memory")


class _Rec:
    def __init__(self):
        self.steps = []

    def record_step(self, tid, step):
        self.steps.append(dict(step))


def _agent(rec=None, recall=None):
    ag = SimpleNamespace(AGENT_ID="supervisor",
                         _session_id="sess_t",
                         _recovery=rec,
                         _recall_memory=recall or (lambda q, top_k=5: []))
    return ag


def _tool_steps(rec, name=None):
    return [s for s in (rec.steps if rec else [])
            if s.get("kind") == "tool" and (name is None or s.get("name") == name)]


# ---------- verify 层 ----------

def test_verify_empty_marker_read_only_ok():
    v = _verify.verify("recall_memory", {"query": "ACM"}, EMPTY, READ_ONLY_CAP)
    assert v.status == _verify.VERIFIED
    assert "EMPTY" in (v.reason or "")


def test_verify_empty_marker_non_read_only_rejected():
    """非只读工具出现 EMPTY 标记 → FAILED，绝不放行（防伪造成功）。"""
    v = _verify.verify("send_payment", {}, EMPTY,
                       {"side_effect": "write", "read_only": False})
    assert v.status == _verify.FAILED


# ---------- _invoke_tool 生产端 ----------

def test_invoke_recall_empty_produces_marker():
    out = _te._invoke_tool(_agent(), "recall_memory", {"query": "ACM"})
    assert out == EMPTY


def test_invoke_recall_hit_unchanged():
    """正常命中行为不回归：仍以【召回的相关记忆】返回。"""
    ag = _agent(recall=lambda q, top_k=5: [{"content": "既往记录"}])
    out = _te._invoke_tool(ag, "recall_memory", {"query": "ACM"})
    assert out.startswith("【召回的相关记忆】")
    assert "既往记录" in out


# ---------- _settle 消费端 ----------

def test_settle_empty_read_only_records_step():
    """read_only 成功但空 → 步骤落盘，result_status=EMPTY，字段可追溯。"""
    rec = _Rec()
    text = _te._settle(_agent(rec), {"name": "recall_memory",
                                     "args": {"query": "ACM"}}, EMPTY)
    assert text == EMPTY
    steps = _tool_steps(rec, "recall_memory")
    assert len(steps) == 1
    s = steps[0]
    assert s["result_status"] == "EMPTY"
    assert s["name"] == "recall_memory"
    assert s["key"] and s["args"] == {"query": "ACM"} and "result" in s


def test_settle_hit_records_non_empty():
    """read_only 正常命中 → NON_EMPTY 落盘（不回归）。"""
    rec = _Rec()
    hit = "【召回的相关记忆】\n- 既往记录"
    _te._settle(_agent(rec), {"name": "recall_memory",
                              "args": {"query": "ACM"}}, hit)
    steps = _tool_steps(rec, "recall_memory")
    assert len(steps) == 1 and steps[0]["result_status"] == "NON_EMPTY"


def test_settle_exception_not_recorded():
    """read_only 执行异常 → FAILED，不落成功证据。"""
    rec = _Rec()
    bad = "(工具 recall_memory 执行异常:ValueError)"
    _te._settle(_agent(rec), {"name": "recall_memory",
                              "args": {"query": "ACM"}}, bad)
    assert _tool_steps(rec) == []


def test_settle_timeout_not_recorded():
    """read_only 超时 → FAILED，不落成功证据。"""
    rec = _Rec()
    bad = "(工具 recall_memory 超过 30s 未返回,已跳过本次调用。)"
    _te._settle(_agent(rec), {"name": "recall_memory",
                              "args": {"query": "ACM"}}, bad)
    assert _tool_steps(rec) == []


def test_settle_empty_marker_on_non_read_only_not_recorded(monkeypatch):
    """非只读工具出现 EMPTY 标记 → 防御分支按失败处理，绝不落盘。"""
    monkeypatch.setattr(_te, "_apply_failure_policy",
                        lambda a, c, r: (r, "ERROR"))  # 隔离失败策略重试,单测防御分支
    rec = _Rec()
    text = _te._settle(_agent(rec), {"name": "send_payment",
                                     "args": {}}, EMPTY)
    assert "非只读" in text
    assert _tool_steps(rec) == []


def test_no_success_evidence_without_execution():
    """空结果未真实执行不得生成成功证据：EMPTY 只能由 dispatch 真实执行后产生，
    verify/落盘均以常量等值判定；对非标记的空字符串不产生任何成功记录。"""
    rec = _Rec()
    _te._settle(_agent(rec), {"name": "recall_memory",
                              "args": {"query": "ACM"}}, "")
    assert _tool_steps(rec) == []


def test_web_search_hit_regression():
    """T6-001 web_search 证据链不回归：带 URL 命中 → VERIFIED + NON_EMPTY 落盘。"""
    rec = _Rec()
    hit = "搜索结果：foxclaw 项目主页 https://example.com/foxclaw"
    _te._settle(_agent(rec), {"name": "web_search",
                              "args": {"query": "foxclaw"}}, hit)
    steps = _tool_steps(rec, "web_search")
    assert len(steps) == 1 and steps[0]["result_status"] == "NON_EMPTY"
    assert "https://example.com/foxclaw" in steps[0]["result"]
