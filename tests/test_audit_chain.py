"""审计落盘的哈希链 + recovery 日志脱敏的回归。

审计文件是纯 append 明文,谁能写谁就能改历史;哈希链不改可见性,只让「改一笔 / 删中间
一笔」在 verify 里暴露。recovery 日志则是被回放读回、长期留在磁盘上的工具调用记录,
工具参数里带 token 的情况很常见。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _write_chain(path: Path, records) -> None:
    from foxclaw.core import audit_chain

    prev = audit_chain.GENESIS
    lines = []
    for r in records:
        rec = audit_chain.link(r, prev)
        prev = rec["hash"]
        lines.append(json.dumps(rec, ensure_ascii=False))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── 链 ─────────────────────────────────────────────────────────────

def test_chain_passes(tmp_path):
    from foxclaw.core import audit_chain

    p = tmp_path / "a.jsonl"
    _write_chain(p, [{"verdict": "deny"}, {"verdict": "allow"}, {"verdict": "success"}])
    ok, n, msg = audit_chain.verify(p)
    assert ok and n == 3, msg


def test_tampered_line_detected(tmp_path):
    from foxclaw.core import audit_chain

    p = tmp_path / "a.jsonl"
    _write_chain(p, [{"verdict": "deny"}, {"verdict": "allow"}])
    lines = p.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["verdict"] = "allow"                     # 把一次拒绝抹成放行
    lines[0] = json.dumps(rec, ensure_ascii=False)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, _, msg = audit_chain.verify(p)
    assert not ok and "被改" in msg, msg


def test_deleted_middle_line_detected(tmp_path):
    from foxclaw.core import audit_chain

    p = tmp_path / "a.jsonl"
    _write_chain(p, [{"i": 0}, {"i": 1}, {"i": 2}])
    lines = p.read_text(encoding="utf-8").splitlines()
    p.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")

    ok, _, msg = audit_chain.verify(p)
    assert not ok and "链断" in msg, msg


def test_resume_after_restart(tmp_path):
    """进程重启后内存链尾丢失,新 auditor 应从文件尾部续链,而不是重开 GENESIS。"""
    from foxclaw.core.governance_audit import GovernanceAuditor

    p = tmp_path / "governance.jsonl"
    GovernanceAuditor(sink_path=p).record(
        layer="skill", actor="office", action="write_note", verdict="success")
    aud = GovernanceAuditor(sink_path=p)          # 模拟重启
    aud.record(layer="skill", actor="office", action="write_note", verdict="success")

    ok, n, msg = aud.verify()
    assert ok and n == 2, msg
    recs = aud.read()
    assert recs[1]["hash"] != recs[0]["hash"]


def test_legacy_lines_skipped(tmp_path):
    """老文件(无 hash)不能因为「没链」就报错;新记录从 GENESIS 起头。"""
    from foxclaw.core import audit_chain

    p = tmp_path / "a.jsonl"
    p.write_text('{"legacy": true}\n', encoding="utf-8")
    assert audit_chain.verify(p)[0] is True
    assert audit_chain.tail_hash(p) == audit_chain.GENESIS


def test_tail_hash_ignores_torn_line(tmp_path):
    from foxclaw.core import audit_chain

    p = tmp_path / "a.jsonl"
    p.write_text('{"a": 1, "hash": "h1"}\n{"half', encoding="utf-8")
    assert audit_chain.tail_hash(p) == "h1"


# ── recovery 脱敏 ──────────────────────────────────────────────────

def test_recovery_step_args_and_result_redacted():
    from types import SimpleNamespace

    from foxclaw.agents.mixins.tool_exec import _record_step, _step_key

    key = "sk-" + "a" * 24
    got = {}

    class _Rec:
        @staticmethod
        def record_step(tid, rec):
            got.update(rec)

    agent = SimpleNamespace(_recovery=_Rec(), _session_id="s1")
    _record_step(agent, {"name": "http", "args": {"api_key": key}},
                 f"ok token=sk-{'b' * 24}")

    assert "sk-" not in json.dumps(got["args"]), got["args"]
    assert "[已脱敏]" in got["result"], got["result"]
    # key 必须用未脱敏的 args 算,否则同一步骤前后 key 不一致(缓存/回放对不上)
    assert got["key"] == _step_key("http", {"api_key": key})
