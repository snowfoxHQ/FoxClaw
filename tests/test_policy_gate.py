"""
统一准入门(PolicyGate, v2 ① Step4)测试 —— 全离线。

覆盖:
  1. 推理准入归一化:Harness PASS/WARN/BLOCK → Verdict allow/warn/deny(raw 透传同一 HarnessResult)
  2. 动作准入归一化:permission allow/ask/deny → Verdict allow/confirm/deny(raw 透传);
     非 allow 发统一 policy.decision.* 事件,allow 不发(降噪)
  3. 真实集成:经 Foxclaw,技能路径(副作用 skill 需确认)发出 policy.decision 事件
"""
import os, sys, asyncio, tempfile, shutil
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_EMBEDDING_MODEL"] = "foxclaw-nonexistent-fast"
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.kernel import PolicyGate


class _HR:                          # 假 HarnessResult
    def __init__(self, blocked=False, warned=False, reason=""):
        self._b, self._w, self.reason = blocked, warned, reason
    @property
    def is_blocked(self): return self._b
    @property
    def is_warned(self): return self._w


class _Harness:
    def __init__(self, hr): self.hr = hr; self.calls = 0
    def check(self, node): self.calls += 1; return self.hr


class _PD:                          # 假 PermissionDecision
    def __init__(self, decision, reason=""):
        self.decision, self.stage, self.reason, self.trust_downgrade = decision, 2, reason, False


class _CapSpine:
    def __init__(self): self.events = []
    def publish(self, topic, payload=None, source="", **kw): self.events.append(topic)


class _Skill:
    id = "gmail_send"


def test_reasoning_normalization():
    print("\n【1】推理准入归一化(Harness → Verdict;raw 透传)")
    gate = PolicyGate()
    h = _Harness(_HR())
    v = gate.check_reasoning(h, None)
    assert v.level == "allow" and v.allowed and v.raw is h.hr and h.calls == 1
    v = gate.check_reasoning(_Harness(_HR(warned=True)), None)
    assert v.level == "warn" and v.allowed
    v = gate.check_reasoning(_Harness(_HR(blocked=True, reason="越界")), None)
    assert v.level == "deny" and not v.allowed and v.reason == "越界"
    print("  ✓ PASS→allow / WARN→warn / BLOCK→deny;raw 仍是同一 HarnessResult(行为一致)")


def test_action_normalization_and_event():
    print("\n【2】动作准入归一化 + 非 allow 发 policy.decision")
    for decision, level, event in [("allow", "allow", None),
                                    ("ask", "confirm", "policy.decision.confirm"),
                                    ("deny", "deny", "policy.decision.deny")]:
        spine = _CapSpine()
        pd = _PD(decision, reason=f"r-{decision}")
        gate = PolicyGate(spine=spine, action_evaluator=lambda *a, _pd=pd, **k: _pd)
        v = gate.check_action(_Skill(), {})
        assert v.level == level and v.raw is pd, f"{decision}→{v.level}"
        assert v.allowed == (decision == "allow")
        assert spine.events == ([event] if event else []), (decision, spine.events)
    print("  ✓ allow(不发) / ask→confirm(发) / deny(发);raw 透传 PermissionDecision")


def test_integration_skill_path():
    print("\n【3】真实集成:技能路径经统一门,副作用 skill 发 policy.decision")
    d = tempfile.mkdtemp(prefix="pg_")
    try:
        from foxclaw import Foxclaw
        os_ = Foxclaw(data_dir=d, shadow_rounds=50)
        seen = []
        os_.event_spine.project(lambda e: seen.append(e.topic))
        # 发邮件是副作用 skill → 需确认 → 经 PolicyGate 发 policy.decision.confirm
        os_.supervisor.use_skill("帮我发邮件给客户",
                                args={"to": "a@b.com", "subject": "x", "body": "y"})
        assert any(t.startswith("policy.decision") for t in seen), \
            f"技能准入应发 policy.decision,实得 {[t for t in seen if 'policy' in t]}"
        assert os_.capabilities.get("policy_gate") is os_.policy_gate
        print(f"  ✓ use_skill(副作用)→ 发 {[t for t in seen if t.startswith('policy')]}")
        asyncio.run(os_.aclose())
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    print("=" * 60)
    print("统一准入门 · PolicyGate(v2 ① Step4)")
    print("=" * 60)
    test_reasoning_normalization()
    test_action_normalization_and_event()
    test_integration_skill_path()
    print("\n" + "=" * 60)
    print("✓ Step4 验证完成:两个门统一到 Verdict 词汇 + 一个接口 + 统一审计")
    print("=" * 60)


if __name__ == "__main__":
    main()
