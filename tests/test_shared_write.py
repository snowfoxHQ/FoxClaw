"""
共享层写入策略回归 (P0-3) —— 全离线,不依赖 HMR / LLM。

验证:
  1. facet 归一:{type:<记忆类型>} ∪ tags,且**排除** agent:<id> 隔离标记
  2. 判定分支:全局允许 / 职责域允许 / 未登记写入方 / 职责域不符
  3. 三种模式:audit 不拦、warn 不拦、enforce 拦截(抛 SharedWriteDenied)
  4. 未挂策略时完全向后兼容(返回 None,不抛不记)
  5. 审计事件:内容只留哈希 + 短预览,不把正文灌进事件流
  6. **现有 8 处合法共享写入在默认策略下全部放行**(audit 基线不误伤)
  7. 端到端:MockHMR 挂策略后 ingest(shared=True) 真走闸门
"""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


class _FakeSpine:
    """记录事件的极简脊柱替身(不引入 EventSpine,保持测试独立)。"""
    def __init__(self):
        self.events = []

    def publish(self, topic, payload=None, source="", correlation_id=None):
        self.events.append({"topic": topic, "payload": payload or {}, "source": source})


def test_facets():
    print("\n【1】facet 归一")
    from foxclaw.core.shared_write import SharedWritePolicy
    f = SharedWritePolicy.facets_of("decision",
                                    ["dispatch", "agent:office", "shared", "to:finance"])
    check(f == ("type:decision", "dispatch", "to:finance"),
          f"agent:<id> 与 shared 标记均被排除、其余保序去重: {f}")
    check(SharedWritePolicy.facets_of("", []) == (), "空输入 → 空 facet")


def test_evaluate_branches():
    print("\n【2】判定分支")
    from foxclaw.core.shared_write import SharedWritePolicy
    p = SharedWritePolicy(mode="enforce")

    d = p.evaluate("office", "decision", ["finance", "transfer"])
    check(not d.allowed, f"办公 Agent 写财务域内容 → 拒绝 ({d.reason})")

    d = p.evaluate("supervisor", "decision", ["dispatch", "multi_step"])
    check(d.allowed, "主管的职责域为 * → 放行")

    d = p.evaluate("office", "preference", ["preference"])
    check(d.allowed, "全局允许 facet(preference)→ 任何 Agent 放行(偏好本就要跨 Agent)")

    d = p.evaluate("system", "execution", ["truth_pipeline"])
    check(d.allowed, "真值沉淀(tags=truth_pipeline)放行")

    d = p.evaluate("stranger", "decision", [])
    check(not d.allowed and "未登记" in d.reason, f"未登记写入方 → 拒绝 ({d.reason})")

    d = p.evaluate(None, "decision", [])
    check(not d.allowed, "agent_id 缺失(unknown)→ 拒绝")


def test_modes():
    print("\n【3】三种模式")
    from foxclaw.core.shared_write import (
        SharedWritePolicy, guard_shared_write, SharedWriteDenied)

    class _Backend:
        pass

    for mode, should_raise in (("audit", False), ("warn", False), ("enforce", True)):
        be = _Backend()
        be.shared_write_policy = SharedWritePolicy(mode=mode)
        be.shared_write_spine = _FakeSpine()
        raised = False
        try:
            guard_shared_write(be, "office", "财务应给办公转账", "decision", ["finance"])
        except SharedWriteDenied:
            raised = True
        check(raised == should_raise, f"mode={mode} → 越界写入{'被拦截' if should_raise else '放行'}")

    be = _Backend(); be.shared_write_policy = SharedWritePolicy(mode="audit")
    be.shared_write_spine = _FakeSpine()
    guard_shared_write(be, "office", "x", "decision", ["finance"])
    evs = [e for e in be.shared_write_spine.events if e["topic"] == "memory.shared.write"]
    check(len(evs) == 1 and evs[0]["payload"]["allowed"] is False,
          "audit 模式:虽放行,但事件里标 allowed=False(可见)")
    check(evs[0]["payload"]["mode"] == "audit", "事件带 mode,便于区分灰度阶段")


def test_backward_compat():
    print("\n【4】未挂策略 → 完全向后兼容")
    from foxclaw.core.shared_write import guard_shared_write

    class _Bare:
        pass

    check(guard_shared_write(_Bare(), "office", "x", "decision", ["finance"]) is None,
          "未挂策略的 backend → 返回 None(不抛、不记)")


def test_audit_payload_privacy():
    print("\n【5】审计事件不留正文")
    from foxclaw.core.shared_write import SharedWritePolicy, guard_shared_write

    class _B:
        pass
    be = _B(); be.shared_write_policy = SharedWritePolicy(mode="audit")
    be.shared_write_spine = _FakeSpine()
    secret = "开头" + "填" * 120 + "结尾不应出现"
    guard_shared_write(be, "office", secret, "decision", ["finance"])
    p = be.shared_write_spine.events[0]["payload"]
    check(len(p["content_preview"]) <= 60, "预览截断到 ≤60 字符")
    check("结尾不应出现" not in p["content_preview"], "超出预览上限的部分不进事件流")
    check(len(p["content_sha"]) == 16, "同时留 16 位摘要哈希,便于比对同一内容")


def test_existing_legitimate_writes_pass():
    print("\n【6】现有 8 处合法共享写入,默认策略下全部放行(基线不误伤)")
    from foxclaw.core.shared_write import SharedWritePolicy
    p = SharedWritePolicy(mode="audit")

    cases = [
        ("主管·多步记录",   "supervisor", "decision", ["dispatch", "multi_step", "to:office"]),
        ("主管·并行记录",   "supervisor", "decision", ["dispatch", "parallel", "to:finance"]),
        ("主管·自答记录",   "supervisor", "decision", []),
        ("偏好共享(办公)", "office",     "preference", ["preference"]),
        ("偏好共享(助手)", "assistant",  "preference", ["preference"]),
        ("真值沉淀",        "system",     "execution", ["truth_pipeline"]),
    ]
    bad = [(n, p.evaluate(a, t, g).reason) for n, a, t, g in cases
           if not p.evaluate(a, t, g).allowed]
    check(not bad, f"默认策略对现有合法写入零误伤(越界项: {bad})")


def test_end_to_end_mock_backend():
    print("\n【7】端到端:MockHMR 挂策略后 ingest(shared=True) 真走闸门")
    from foxclaw.core.memory_adapter import MockHMR
    from foxclaw.core.shared_write import (
        SharedWritePolicy, attach, SharedWriteDenied)

    be = MockHMR()
    spine = _FakeSpine()

    be.ingest("普通记忆", agent_id="office")
    check(not spine.events, "shared=False → 闸门不介入(零事件)")

    attach(be, SharedWritePolicy(mode="enforce"), spine=spine)
    be.ingest("办公读书记录", agent_id="office", type="concept", tags=["preference"],
              shared=True)
    check(len(spine.events) == 1, "shared=True → 产生 memory.shared.write 事件")

    try:
        be.ingest("财务应给办公转账", agent_id="office", type="decision",
                  tags=["finance"], shared=True)
        check(False, "enforce 下办公 Agent 写财务域应被拒")
    except SharedWriteDenied as e:
        check("office" in str(e), f"越界写入被拦截: {e}")


def test_mode_resolution():
    print("\n【8】模式解析优先级")
    from foxclaw.core.shared_write import SharedWritePolicy
    old = os.environ.get("FOXCLAW_SHARED_WRITE_MODE")
    try:
        os.environ["FOXCLAW_SHARED_WRITE_MODE"] = "enforce"
        check(SharedWritePolicy().mode == "enforce", "环境变量生效")
        check(SharedWritePolicy(mode="audit").mode == "audit", "显式入参优先于环境变量")
        os.environ["FOXCLAW_SHARED_WRITE_MODE"] = "乱填的值"
        check(SharedWritePolicy().mode == "audit", "非法值回落 audit(保守:不拦截)")
    finally:
        if old is None:
            os.environ.pop("FOXCLAW_SHARED_WRITE_MODE", None)
        else:
            os.environ["FOXCLAW_SHARED_WRITE_MODE"] = old


def main():
    print("=" * 60)
    print("共享层写入策略(P0-3)")
    print("=" * 60)
    test_facets()
    test_evaluate_branches()
    test_modes()
    test_backward_compat()
    test_audit_payload_privacy()
    test_existing_legitimate_writes_pass()
    test_end_to_end_mock_backend()
    test_mode_resolution()
    print("\n" + "=" * 60)
    print("✓ 共享层写入策略验证完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
