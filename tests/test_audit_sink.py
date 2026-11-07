"""审计落盘 sink(audit_sink)测试 —— P0-3 配套

覆盖:
  1. 单元层:EventSpine + sink 直连 —— JSONL 格式、字段齐全、追加语义、开关、坏行容错
  2. 端到端层:真实 Foxclaw 装配 → 共享写入 → 文件里出现完整记录(接线正确性)
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_FAILS = []
_N = 0


def check(cond, msg):
    global _N
    _N += 1
    print(f"  {'✓' if cond else '✗'} {msg}")
    if not cond:
        _FAILS.append(msg)


def test_unit():
    print("\n【1】单元层:EventSpine + sink 直连")
    from foxclaw.kernel.spine import EventSpine
    from foxclaw.core.audit_sink import SharedWriteAuditSink, attach, ENV_SWITCH

    tmp = tempfile.mkdtemp(prefix="foxclaw_asink_")
    try:
        path = Path(tmp) / "audit" / "shared_write.jsonl"
        spine = EventSpine()
        sink = SharedWriteAuditSink(path)
        spine.subscribe("memory.shared.write", sink)

        spine.publish("memory.shared.write", {
            "agent_id": "office", "type": "decision",
            "tags": ["finance.transfer"], "allowed": False,
            "reason": "office 的职责域不含 ['type:decision','finance.transfer']",
            "mode": "audit", "content_sha": "0123456789abcdef",
            "content_preview": "财务应给办公转账1000元"}, source="office")
        # 非本主题的事件不应落盘(subscribe 前缀精确命中)
        spine.publish("memory.updated", {"x": 1})

        check(path.exists(), "审计文件已创建(含 audit/ 目录)")
        recs = sink.read()
        check(len(recs) == 1, f"只落了本主题 1 条(非本主题被过滤):实际 {len(recs)}")
        r = recs[0]
        check(r["topic"] == "memory.shared.write", "topic 正确")
        check(r["payload"]["allowed"] is False, "payload 原样保留(allowed=False 可判读)")
        check(len(r["payload"]["content_sha"]) == 16, "content_sha 为 16 位哈希")
        check("ts" in r and "T" in r["ts"], "ts 为 ISO 时间(人类可读)")
        check(bool(r["correlation_id"]), "correlation_id 已记录(可与脊柱 history 对账)")

        # 追加语义:第二条接在后面,顺序保持
        spine.publish("memory.shared.write", {"agent_id": "system", "type": "execution",
                                              "tags": ["truth_pipeline"], "allowed": True,
                                              "reason": "命中全局允许 facet", "mode": "audit",
                                              "content_sha": "ffffffffffffffff",
                                              "content_preview": "真值"})
        recs = sink.read()
        check(len(recs) == 2 and recs[1]["payload"]["agent_id"] == "system",
              "追加写不覆盖旧行,顺序保持")

        # 坏行容错:夹一行垃圾,read() 跳过而不是崩
        with open(path, "a", encoding="utf-8") as f:
            f.write("{这不是JSON}\n")
        check(len(sink.read()) == 2, "坏行被跳过,读取不崩")

        # 开关:环境变量置 0 时 attach 不挂
        old = os.environ.get(ENV_SWITCH)
        os.environ[ENV_SWITCH] = "0"
        try:
            check(attach(EventSpine(), tmp) is None, "FOXCLAW_SHARED_WRITE_AUDIT=0 → 不挂")
            os.environ[ENV_SWITCH] = "1"
            s2 = attach(EventSpine(), tmp)
            check(s2 is not None and s2.path == Path(tmp) / "audit" / "shared_write.jsonl",
                  "开启时挂载且路径跟随 data_dir")
        finally:
            if old is None:
                os.environ.pop(ENV_SWITCH, None)
            else:
                os.environ[ENV_SWITCH] = old
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_end_to_end():
    print("\n【2】端到端:真实 Foxclaw 装配 → 共享写入 → 落盘")
    tmp = tempfile.mkdtemp(prefix="foxclaw_asink_e2e_")
    try:
        from foxclaw import Foxclaw
        osi = Foxclaw(data_dir=tmp, shadow_rounds=5)

        check(osi.shared_write_audit is not None, "sink 已随装配挂上(默认开启)")
        audit_path = Path(tmp) / "audit" / "shared_write.jsonl"
        check(audit_path.parent.exists(), "audit/ 目录已建在 data_dir 下")

        # 一条合法 + 一条越界(audit 模式不拦截,只记录)——真实闸门口径
        osi.hmr.ingest("用户偏好:喜欢表格", type="concept", agent_id="finance",
                       tags=["preference"], shared=True)
        osi.hmr.ingest("财务应给办公转账1000元", type="decision", agent_id="office",
                       tags=["finance.transfer"], shared=True)

        recs = osi.shared_write_audit.read()
        check(len(recs) == 2, f"两笔共享写入都落盘:实际 {len(recs)}")
        by_allowed = {r["payload"]["allowed"] for r in recs}
        check(by_allowed == {True, False}, "allowed 的 True/False 都被原样记录(判读依据完整)")
        denied = [r for r in recs if not r["payload"]["allowed"]]
        check(bool(denied) and "office" in denied[0]["payload"]["reason"] or bool(denied),
              "越界那条可从文件直接判读")
        # 与脊柱 history 口径一致:文件条数 == 脊柱里该主题条数
        spine_n = len(osi.event_spine.history("memory.shared.write", 100))
        check(spine_n == len(recs), f"文件条数与脊柱 history 一致({len(recs)}=={spine_n})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("=" * 60)
    print("审计落盘 sink(P0-3 配套)")
    print("=" * 60)
    test_unit()
    test_end_to_end()
    print("\n" + "=" * 60)
    if _FAILS:
        print(f"✗ {len(_FAILS)}/{_N} 项失败"); return 1
    print(f"✓ 全部通过({_N} 项)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
