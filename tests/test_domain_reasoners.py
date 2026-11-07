"""
领域认知化推广(v2 ③ Step4:itinerary / learning 复用 DomainReasoner)—— 全离线。

同款双层安全不变量(与 finance 一致):
  · 规则永远权威:检出硬冲突/高遗忘风险 → confirm,**绝不被 LLM 推翻/隐藏**,且不问 LLM;
  · 可选 LLM 层默认关,只对"无硬问题、但紧凑/负荷重"的微妙情形**升级** approve→confirm;
  · LLM 结构上无从产生"推翻规则"结果(helper 只返回 escalate/keep);
  · 顾问域 fail-safe:LLM 出错/stub/默认关 → 维持规则裁决(approve),不制造确认噪声;
  · 每个裁决带证据链(可审计)。
"""
import os, sys
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["FOXCLAW_EMBEDDING_MODEL"] = "foxclaw-nonexistent-fast"
sys.path.insert(0, str(Path(__file__).parent.parent))


class _MockLLM:
    """可配置回答的假 LLM(is_stub=False 表示"有真实模型")。"""
    def __init__(self, resp="OK", raises=False):
        self.resp, self.raises, self.calls, self.is_stub = resp, raises, 0, False
    def complete(self, system, messages, max_tokens=2048, agent_id=""):
        self.calls += 1
        if self.raises:
            raise RuntimeError("llm down")
        return self.resp


# ─────────────────────────── 行程冲突推理 ───────────────────────────
def _wp(name, start=None, dur=0, lng=None, lat=None):
    from foxclaw.itinerary.models import Waypoint
    return Waypoint(name=name, start_time=start, duration_min=dur, lng=lng, lat=lat)


def test_itinerary_reasoner():
    print("\n【1】行程冲突推理:规则权威 + 可选 LLM 只升级")
    from foxclaw.itinerary.reasoner import ItineraryConflictReasoner
    # 时间重叠 → confirm,规则权威;即便 LLM 说 OK 也不被推翻,且不问 LLM
    conflict = [_wp("会议A", "2026-07-08T10:00:00", 120), _wp("会议B", "2026-07-08T11:00:00", 60)]
    llm = _MockLLM("OK")
    rv = ItineraryConflictReasoner(llm=llm, llm_enabled=True).reason((conflict, "driving"))
    assert rv.decision == "confirm" and rv.source == "rules" and llm.calls == 0
    assert any(e.factor == "time_conflict" for e in rv.evidence)

    # 无冲突、不紧凑(空档 60 分 >> 通勤 ~19 分)→ approve,LLM 不调
    loose = [_wp("A", "2026-07-08T10:00:00", 60, 116.40, 39.90),
             _wp("B", "2026-07-08T12:00:00", 60, 116.50, 39.90)]
    llm = _MockLLM("CONFIRM")
    rv = ItineraryConflictReasoner(llm=llm, llm_enabled=True).reason((loose, "driving"))
    assert rv.decision == "approve" and rv.source == "rules" and llm.calls == 0

    # 紧凑(空档 25 分,通勤 ~19 分,可达但赶)+ LLM 说 CONFIRM → confirm,source=llm
    tight = [_wp("A", "2026-07-08T10:00:00", 60, 116.40, 39.90),
             _wp("B", "2026-07-08T11:25:00", 60, 116.50, 39.90)]
    llm = _MockLLM("CONFIRM")
    rv = ItineraryConflictReasoner(llm=llm, llm_enabled=True).reason((tight, "driving"))
    assert rv.decision == "confirm" and rv.source == "llm" and llm.calls == 1

    # 紧凑 + LLM 说 OK → 维持规则 approve
    llm = _MockLLM("OK")
    rv = ItineraryConflictReasoner(llm=llm, llm_enabled=True).reason((tight, "driving"))
    assert rv.decision == "approve" and rv.source == "rules"

    # 紧凑 + LLM 出错 → fail-safe 维持规则 approve(顾问域不因抖动制造确认)
    llm = _MockLLM(raises=True)
    rv = ItineraryConflictReasoner(llm=llm, llm_enabled=True).reason((tight, "driving"))
    assert rv.decision == "approve" and rv.source == "rules"

    # 默认关 → 紧凑也只走规则(behavior-preserving)
    rv = ItineraryConflictReasoner().reason((tight, "driving"))
    assert rv.decision == "approve" and rv.source == "rules"
    print("  ✓ 冲突→confirm(规则,不问LLM);紧凑+LLM升级→confirm(llm);OK/出错/默认关→approve(规则)")


# ─────────────────────────── 学习复习推理 ───────────────────────────
def _rs(item_id, rep, ease, due_days):
    from datetime import datetime, timedelta
    from foxclaw.learning.models import ReviewState
    return ReviewState(item_id=item_id, repetitions=rep, ease_factor=ease,
                       due_date=(datetime.now() + timedelta(days=due_days)).isoformat())


def test_learning_reasoner():
    print("\n【2】学习复习推理:规则权威 + 可选 LLM 只升级")
    from foxclaw.learning.reasoner import LearningReviewReasoner
    # 高遗忘风险(掌握度 0、已到期)→ confirm,规则权威,不问 LLM
    risky = [_rs("weak", 0, 1.3, 0)]
    llm = _MockLLM("OK")
    rv = LearningReviewReasoner(llm=llm, llm_enabled=True).reason(risky)
    assert rv.decision == "confirm" and rv.source == "rules" and llm.calls == 0
    assert any(e.factor == "forgetting_risk" for e in rv.evidence)

    # 健康(高掌握、远期到期)→ approve,LLM 不调
    healthy = [_rs("strong", 8, 2.5, 30)]
    llm = _MockLLM("CONFIRM")
    rv = LearningReviewReasoner(llm=llm, llm_enabled=True).reason(healthy)
    assert rv.decision == "approve" and rv.source == "rules" and llm.calls == 0

    # 负荷偏重但无高风险(150 项高掌握、近期到期,日均≈21≥20)+ LLM CONFIRM → confirm,source=llm
    heavy = [_rs(f"h{i}", 8, 2.5, 3) for i in range(150)]
    llm = _MockLLM("CONFIRM")
    rv = LearningReviewReasoner(llm=llm, llm_enabled=True).reason(heavy)
    assert rv.decision == "confirm" and rv.source == "llm" and llm.calls == 1

    # 负荷重 + LLM OK → approve;+ LLM 出错 → fail-safe approve
    rv = LearningReviewReasoner(llm=_MockLLM("OK"), llm_enabled=True).reason(heavy)
    assert rv.decision == "approve" and rv.source == "rules"
    rv = LearningReviewReasoner(llm=_MockLLM(raises=True), llm_enabled=True).reason(heavy)
    assert rv.decision == "approve" and rv.source == "rules"

    # 默认关 + stub → 只走规则
    rv = LearningReviewReasoner().reason(heavy)
    assert rv.decision == "approve" and rv.source == "rules"
    stub = _MockLLM("CONFIRM"); stub.is_stub = True
    rv = LearningReviewReasoner(llm=stub, llm_enabled=True).reason(heavy)
    assert rv.source == "rules" and stub.calls == 0
    print("  ✓ 高风险→confirm(规则,不问LLM);负荷重+LLM升级→confirm(llm);OK/出错/默认/stub→approve(规则)")


def test_llm_cannot_hide_hard_problems():
    print("\n【3】核心不变量:LLM 永远无法隐藏规则已检出的硬问题")
    from foxclaw.itinerary.reasoner import ItineraryConflictReasoner
    from foxclaw.learning.reasoner import LearningReviewReasoner
    # 即使 LLM 疯狂回 "OK/APPROVE/一切正常",冲突/风险仍被规则裁为 confirm
    for bad in ["OK", "APPROVE", "一切正常没问题", ""]:
        conflict = [_wp("A", "2026-07-08T10:00:00", 120), _wp("B", "2026-07-08T11:00:00", 60)]
        rv = ItineraryConflictReasoner(llm=_MockLLM(bad), llm_enabled=True).reason((conflict, "driving"))
        assert rv.decision == "confirm" and rv.source == "rules", (bad, rv.decision)
        rv = LearningReviewReasoner(llm=_MockLLM(bad), llm_enabled=True).reason([_rs("w", 0, 1.3, 0)])
        assert rv.decision == "confirm" and rv.source == "rules", (bad, rv.decision)
    print("  ✓ 任何 LLM 回答都无法把已检出的冲突/高风险降级为 approve")


def main():
    print("=" * 60)
    print("领域认知化推广 · itinerary + learning(v2 ③ Step4)")
    print("=" * 60)
    test_itinerary_reasoner()
    test_learning_reasoner()
    test_llm_cannot_hide_hard_problems()
    print("\n" + "=" * 60)
    print("✓ Step4:两域复用 DomainReasoner;规则权威、LLM 只增强、证据链可审计")
    print("=" * 60)


if __name__ == "__main__":
    main()
