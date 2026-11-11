"""
高敏感记忆写入闸门测试 —— 全离线。

覆盖:
  1. 四类高敏感特征(凭据/证件/健康/账户)都能确定性命中,且样本在事件里是打码的。
  2. 不误伤日常内容:普通金额、未过 Luhn 的订单号不命中(否则财务 Agent 无法使用)。
  3. 三档模式:audit/warn 不拦截;enforce 拦截并把内容转成待确认项(不丢)。
  4. 待确认队列落盘、可取回、可移除;重启后仍在。
  5. 未挂策略时 guard 返回 None(向后兼容)。
"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.core.sensitive_write import (
    MODE_ENV,
    SensitivityClassifier,
    SensitiveWritePolicy,
    attach,
    guard_sensitive_write,
)

POSITIVE = {
    "credential": "我的网银密码是 Abc123456,别记错",
    "identity": "身份证号 110101199003071234",
    "health": "医生诊断为高血压,需要长期服药",
    "finance_detail": "卡号 4111111111111111 是工资卡",
}


def test_each_category_is_detected():
    c = SensitivityClassifier()
    for cat, text in POSITIVE.items():
        hits = c.classify(text)
        assert hits, f"{cat} 未命中:{text}"
        assert cat in {h.category for h in hits}


def test_plain_amounts_and_order_numbers_are_not_high_sensitivity():
    c = SensitivityClassifier()
    for text in ("这个月餐饮花了 3200 元", "订单号 2026111200012345", "余额还剩 12345.67"):
        assert c.classify(text) == (), f"误判:{text}"


def test_card_number_requires_luhn():
    c = SensitivityClassifier()
    assert c.classify("4111 1111 1111 1111")
    assert c.classify("1234567890123456") == ()


def test_sample_is_masked():
    hits = SensitivityClassifier().classify("密码是 Abc123456")
    assert hits and "Abc123456" not in hits[0].sample


def test_audit_mode_does_not_block(tmp_path):
    p = SensitiveWritePolicy("audit", pending_path=tmp_path / "p.json")
    d = p.evaluate(POSITIVE["identity"])
    assert d.allowed is True and d.mode == "audit"
    assert d.categories == ("identity",)


def test_enforce_mode_blocks_and_stashes(tmp_path):
    backend = SimpleNamespace(sensitive_write_policy=SensitiveWritePolicy(
        "enforce", pending_path=tmp_path / "p.json"))
    d = guard_sensitive_write(backend, "finance", POSITIVE["health"], "concept")
    assert d.allowed is False and d.is_pending
    items = json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))
    assert [it["id"] for it in items] == [d.pending_id]
    assert "高血压" in items[0]["content"]
    assert items[0]["agent_id"] == "finance"


def test_clean_content_passes_in_enforce_mode(tmp_path):
    backend = SimpleNamespace(sensitive_write_policy=SensitiveWritePolicy(
        "enforce", pending_path=tmp_path / "p.json"))
    d = guard_sensitive_write(backend, "finance", "本月餐饮 3200 元", "concept")
    assert d.allowed is True and not d.is_pending


def test_pending_survives_reload_and_can_be_popped(tmp_path):
    path = tmp_path / "p.json"
    backend = SimpleNamespace(sensitive_write_policy=SensitiveWritePolicy(
        "enforce", pending_path=path))
    d = guard_sensitive_write(backend, "office", POSITIVE["credential"], "concept")
    reopened = SensitiveWritePolicy("enforce", pending_path=path)
    got = reopened.pending.pop(d.pending_id)
    assert got is not None and got["agent_id"] == "office"
    assert reopened.pending.pop(d.pending_id) is None


def test_no_policy_is_backward_compatible():
    assert guard_sensitive_write(SimpleNamespace(), "finance", POSITIVE["credential"]) is None


def test_attach_binds_policy_to_backend(tmp_path):
    backend = SimpleNamespace()
    attach(backend, SensitiveWritePolicy("warn", pending_path=tmp_path / "p.json"))
    assert backend.sensitive_write_policy.mode == "warn"


def test_mode_env(monkeypatch, tmp_path):
    monkeypatch.setenv(MODE_ENV, "enforce")
    assert SensitiveWritePolicy(pending_path=tmp_path / "p.json").mode == "enforce"
    monkeypatch.setenv(MODE_ENV, "乱写的")
    assert SensitiveWritePolicy(pending_path=tmp_path / "p.json").mode == "audit"


def test_event_carries_no_raw_secret(tmp_path):
    events = []
    backend = SimpleNamespace(
        sensitive_write_policy=SensitiveWritePolicy("audit", pending_path=tmp_path / "p.json"),
        sensitive_write_spine=SimpleNamespace(publish=lambda n, d, source="": events.append((n, d))))
    guard_sensitive_write(backend, "finance", POSITIVE["credential"], "concept")
    assert events and events[0][0] == "memory.sensitive"
    blob = json.dumps(events[0][1], ensure_ascii=False)
    assert "Abc123456" not in blob


def _backend(tmp_path, mode="enforce"):
    from foxclaw.core.memory_adapter import MockHMR
    b = MockHMR()
    attach(b, SensitiveWritePolicy(mode, pending_path=tmp_path / "p.json"))
    return b


def test_adapter_ingest_is_blocked_in_enforce_mode(tmp_path):
    b = _backend(tmp_path)
    r = b.ingest(POSITIVE["identity"], type="concept", agent_id="finance")
    assert getattr(r, "pending", False) and r.pending_id
    assert b.sensitive_write_policy.pending.list()


def test_adapter_ingest_passes_clean_content(tmp_path):
    b = _backend(tmp_path)
    r = b.ingest("本月餐饮 3200 元", type="concept", agent_id="finance")
    assert not getattr(r, "pending", False) and r.id


def test_consent_flag_bypasses_gate(tmp_path):
    b = _backend(tmp_path)
    r = b.ingest(POSITIVE["identity"], type="concept", agent_id="finance", consent=True)
    assert not getattr(r, "pending", False)


def _fake_os(tmp_path, mode="enforce"):
    from foxclaw import Foxclaw
    b = _backend(tmp_path, mode)
    fake = SimpleNamespace(hmr=b, sensitive_write_policy=b.sensitive_write_policy)
    for name in ("list_sensitive_pending", "approve_sensitive_write", "reject_sensitive_write"):
        setattr(fake, name, getattr(Foxclaw, name).__get__(fake, type(fake)))
    return fake


def test_approve_writes_through_and_clears_pending(tmp_path):
    os_ = _fake_os(tmp_path)
    pid = os_.hmr.ingest(POSITIVE["health"], type="concept", agent_id="finance").pending_id
    assert [it["id"] for it in os_.list_sensitive_pending()] == [pid]
    assert os_.approve_sensitive_write(pid) is True
    assert os_.list_sensitive_pending() == []
    assert os_.approve_sensitive_write(pid) is False


def test_reject_discards_without_writing(tmp_path):
    os_ = _fake_os(tmp_path)
    pid = os_.hmr.ingest(POSITIVE["credential"], type="concept", agent_id="office").pending_id
    assert os_.reject_sensitive_write(pid) is True
    assert os_.list_sensitive_pending() == []
    assert os_.reject_sensitive_write("nope") is False


def test_audit_mode_keeps_business_running(tmp_path):
    b = _backend(tmp_path, "audit")
    r = b.ingest(POSITIVE["health"], type="concept", agent_id="finance")
    assert not getattr(r, "pending", False) and r.id
    assert b.sensitive_write_policy.pending.list() == []
