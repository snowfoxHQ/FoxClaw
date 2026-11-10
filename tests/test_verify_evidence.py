"""三个薄点:失败策略表 / 执行后核验 / 执行证据。

锁住的行为(对应审核报告里「静默假成功」那一类):
  ① 失败文本能归类成明确类型,而不是"模型自己看着办";
  ② 只有**只读**工具会自动重试一次 —— 有副作用的动作重试等于重复扣款/重复发消息;
  ③ 每步都有核验结论:有凭据=verified,没后置条件=unverified,失败=failed,**空结果不算成功**;
  ④ 证据只从真实返回里抽 URL / 磁盘上存在的文件,认不出的路径不冒充证据;
  ⑤ 这些结论挂到会话槽并由 `_run_tool_calls` 记账,不上屏到用户正文;
  ⑥ 成功结果的回注文本一个字都不改(否则会污染既有工具轮行为)。
"""

from __future__ import annotations

import types

import pytest

from foxclaw.agents.mixins import tool_exec
from foxclaw.core import failure_policy as policy
from foxclaw.core import tools as core_tools
from foxclaw.core import verify as vf

TIMEOUT_TEXT = "(工具 web_search 超过 45s 未返回,已跳过以免拖住整轮)"


class _Rec:
    def __init__(self):
        self.steps: list = []

    def record_step(self, tid, step):
        self.steps.append((tid, step))


def _agent():
    a = types.SimpleNamespace()
    a._recovery = _Rec()
    a._session_id = "sess_test"
    return a


@pytest.mark.parametrize("text,kind", [
    (TIMEOUT_TEXT, policy.TIMEOUT),
    ("(工具 shell 已被策略禁用)", policy.DENIED),
    ("(联网搜索未配置,已降级)", policy.NOT_CONFIGURED),
    ("(入参不合法:缺少必填 query)", policy.INVALID_ARGS),
    ("(联网搜索未返回结果)", policy.EMPTY),
    ("(工具 web_search 执行异常:boom)", policy.EXCEPTION),
    ("中国未来十年的产业趋势如何", policy.OK),
    ("", policy.UNKNOWN),
    ("一切正常", policy.OK),
])
def test_classify(text, kind) -> None:
    assert policy.classify(text) == kind


def test_retry_only_for_read_only() -> None:
    read = policy.decide(TIMEOUT_TEXT, capability={"read_only": True}, attempt=1)
    assert read.action == policy.RETRY
    assert policy.decide(TIMEOUT_TEXT, capability={"read_only": True}, attempt=2).action \
        == policy.HAND_TO_HUMAN
    write = policy.decide(TIMEOUT_TEXT, capability={"read_only": False}, attempt=1)
    assert write.action == policy.HAND_TO_HUMAN
    assert "重复执行" in write.hint


def test_decide_never_optimistic() -> None:
    assert policy.decide("(联网搜索未配置)").action == policy.DEGRADE
    assert policy.decide("(工具 x 已被策略禁用)").action == policy.STOP
    assert policy.decide("(看不懂的回注").kind == policy.UNKNOWN
    assert policy.decide("(看不懂的回注").action == policy.HAND_TO_HUMAN


def test_annotate_leaves_success_untouched() -> None:
    d = policy.decide("搜索到的正文", capability={"read_only": True})
    assert policy.annotate("搜索到的正文", d) == "搜索到的正文"
    out = policy.annotate(TIMEOUT_TEXT, policy.decide(TIMEOUT_TEXT, capability={"read_only": True},
                                                     attempt=2))
    assert out.startswith(TIMEOUT_TEXT)
    assert "[执行策略]" in out


def test_failure_is_failed_not_verified() -> None:
    v = vf.verify("web_search", {}, TIMEOUT_TEXT, {"read_only": True})
    assert v.status == vf.FAILED
    assert v.reason.startswith("失败类型")


def test_empty_result_is_not_success() -> None:
    v = vf.verify("web_search", {}, "", {"read_only": True})
    assert v.status == vf.FAILED


def test_read_tool_with_payload_is_verified(tmp_path) -> None:
    p = tmp_path / "report.md"
    p.write_text("x", encoding="utf-8")
    v = vf.verify("read_file", {}, f"见 {p}", {"read_only": True})
    assert v.status == vf.VERIFIED
    assert [e.kind for e in v.evidence] == ["file"]
    assert v.evidence[0].ref == str(p)


def test_side_effect_without_postcondition_is_unverified() -> None:
    v = vf.verify("send_mail", {}, "已发送", {"read_only": False})
    assert v.status == vf.UNVERIFIED
    assert "后置条件" in v.reason
    assert vf.verify("recall_memory", {}, "【召回的相关记忆】\n- a", {"read_only": True}).status \
        == vf.VERIFIED


def test_web_search_without_source_is_unverified() -> None:
    v = vf.verify("web_search", {}, "【联网搜索结果】据行业报告……", {"read_only": True})
    assert v.status == vf.UNVERIFIED
    assert "来源" in v.reason
    v2 = vf.verify("web_search", {}, "【联网搜索结果】见 https://example.com/a", {"read_only": True})
    assert v2.status == vf.VERIFIED
    assert v2.evidence[0].label == "example.com"


def test_evidence_only_counts_real_paths(tmp_path) -> None:
    ghost = "C:/definitely/not/here/x.md"
    assert vf.extract_evidence("read_file", f"读了 {ghost}") == []
    real = tmp_path / "a.md"
    real.write_text("x", encoding="utf-8")
    ev = vf.extract_evidence("read_file", f"读了 {real}")
    assert [e.ref for e in ev] == [str(real)]


def test_evidence_dedup_and_cap() -> None:
    text = " ".join([f"https://example.com/{i}" for i in range(20)]) + " https://example.com/1"
    ev = vf.extract_evidence("web_search", text)
    assert len(ev) == vf.MAX_EVIDENCE
    assert len({e.ref for e in ev}) == vf.MAX_EVIDENCE


def test_note_for_model() -> None:
    ok = vf.Verification(vf.VERIFIED)
    assert vf.note_for_model(ok) == ""
    un_read = vf.Verification(vf.UNVERIFIED, "无来源")
    assert vf.note_for_model(un_read, read_only=True) == ""
    assert "不要断言" in vf.note_for_model(un_read, read_only=False)
    bad = vf.Verification(vf.FAILED, "失败类型:timeout")
    assert "不要向用户声称" in vf.note_for_model(bad)


def test_retries_read_only_once_then_gives_up(monkeypatch) -> None:
    agent = _agent()
    batches: list = []

    def fake_batch(a, calls):
        batches.append([c["name"] for c in calls])
        return [TIMEOUT_TEXT] * len(calls)

    monkeypatch.setattr(tool_exec, "_run_tools_batch", fake_batch)
    monkeypatch.setattr(core_tools, "tool_capability", lambda n: {"read_only": True})
    out = tool_exec._run_tool_calls(agent, [{"name": "web_search", "args": {"q": "x"}}])

    assert len(batches) == 2
    assert "[执行策略]" in out[0] and "不要" in out[0]
    assert agent._recovery.steps == []
    ev = agent._evidence_list
    assert ev and ev[0]["status"] == vf.FAILED


def test_side_effect_failure_is_not_retried(monkeypatch) -> None:
    agent = _agent()
    batches: list = []
    monkeypatch.setattr(tool_exec, "_run_tools_batch",
                        lambda a, calls: (batches.append(len(calls)), [TIMEOUT_TEXT] * len(calls))[1])
    monkeypatch.setattr(core_tools, "tool_capability", lambda n: {"read_only": False})
    out = tool_exec._run_tool_calls(agent, [{"name": "send_mail", "args": {}}])
    assert batches == [1]
    assert "重复执行" in out[0]


def test_success_text_untouched_and_evidence_recorded(monkeypatch) -> None:
    agent = _agent()
    monkeypatch.setattr(tool_exec, "_run_tools_batch",
                        lambda a, calls: ["【联网搜索结果】结论 https://example.com/a"] * len(calls))
    monkeypatch.setattr(core_tools, "tool_capability", lambda n: {"read_only": True})
    out = tool_exec._run_tool_calls(agent, [{"name": "web_search", "args": {"q": "x"}}])

    assert out[0] == "【联网搜索结果】结论 https://example.com/a"
    assert len(agent._recovery.steps) == 1
    ev = agent._evidence_list[0]
    assert ev["status"] == vf.VERIFIED
    assert ev["evidence"][0]["ref"] == "https://example.com/a"


def test_unverified_side_effect_tells_model_not_to_claim_done(monkeypatch) -> None:
    agent = _agent()
    monkeypatch.setattr(tool_exec, "_run_tools_batch", lambda a, calls: ["已发送"] * len(calls))
    monkeypatch.setattr(core_tools, "tool_capability", lambda n: {"read_only": False})
    out = tool_exec._run_tool_calls(agent, [{"name": "send_mail", "args": {}}])
    assert out[0].startswith("已发送")
    assert "不要断言" in out[0]
    assert agent._evidence_list[0]["status"] == vf.UNVERIFIED


def test_evidence_is_per_agent() -> None:
    from foxclaw.agents.mixins import _context

    a, b = _agent(), _agent()
    _context._evidence_reset(a)
    _context._evidence_reset(b)
    _context._evidence_add(a, {"tool": "x", "status": "verified"})
    assert _context._evidence_take(a) and not _context._evidence_take(b)
    assert _context._evidence_take(a) == []


def test_verify_never_raises_on_weird_input() -> None:
    assert vf.verify("web_search", {}, "(((", None).status == vf.FAILED
    assert vf.verify("unknown_tool", None, "ok", None).status == vf.UNVERIFIED
