"""
Foxclaw 学习子系统 — 间隔重复引擎(固化层)
实现 SM-2 算法(Anki 核心算法的经典版本)。
核心思想:学习是时间连续系统——追踪每个知识点的记忆强度随时间衰减,
在最佳时机安排复习,让"越用越熟"变成可计算的机制。
"""

from datetime import datetime, timedelta
from .models import ReviewState


class SpacedRepetition:
    """SM-2 间隔重复算法。

    评分 grade(0~5):
      0-2 = 没记住(算 lapse,重置间隔)
      3   = 勉强记住
      4   = 记住了
      5   = 完美回忆
    """

    @staticmethod
    def review(state: ReviewState, grade: int) -> ReviewState:
        """根据本次回忆质量,更新间隔重复状态。返回更新后的 state。"""
        # grade 钳制到 SM-2 定义域 [0,5],防止越界输入让 EF 公式算出无意义值
        grade = max(0, min(5, int(grade)))
        now = datetime.now()
        state.total_reviews += 1
        state.last_review = now.isoformat()

        if grade < 3:
            # 没记住:重置,短期内再复习
            state.repetitions = 0
            state.interval = 1
            state.lapses += 1
        else:
            # 记住了:按 SM-2 推进间隔
            if state.repetitions == 0:
                state.interval = 1
            elif state.repetitions == 1:
                state.interval = 6
            else:
                state.interval = round(state.interval * state.ease_factor)
            state.repetitions += 1

        # 更新难度因子(SM-2 公式)
        ef = state.ease_factor + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
        state.ease_factor = max(1.3, round(ef, 3))

        # 下次复习时间
        state.due_date = (now + timedelta(days=state.interval)).isoformat()
        return state

    @staticmethod
    def due_items(states: list) -> list:
        """返回所有到期需复习的状态,按掌握度升序(最弱的先复习)。"""
        due = [s for s in states if s.is_due]
        return sorted(due, key=lambda s: s.mastery)

    @staticmethod
    def forgetting_risk(states: list, horizon_days: int = 3) -> list:
        """遗忘风险检测(对应财务异常检测):
        即将到期且掌握度低的知识点 = 高遗忘风险。"""
        risky = []
        for s in states:
            if s.days_until_due <= horizon_days and s.mastery < 0.5:
                risky.append({
                    "item_id": s.item_id,
                    "mastery": s.mastery,
                    "days_until_due": s.days_until_due,
                    "lapses": s.lapses,
                    "risk": round((1 - s.mastery) * (1 if s.is_due else 0.6), 3),
                    "reason": f"掌握度仅 {s.mastery},"
                              + ("已到期" if s.is_due else f"{s.days_until_due} 天内到期"),
                })
        return sorted(risky, key=lambda x: -x["risk"])

    @staticmethod
    def learning_load(states: list, days: int = 7) -> dict:
        """学习负荷(对应财务烧钱速度):未来 N 天的复习量。"""
        from collections import defaultdict
        by_day: defaultdict[int, int] = defaultdict(int)
        for s in states:
            d = s.days_until_due
            if 0 <= d <= days:
                by_day[d] += 1
            elif d < 0:
                by_day[0] += 1   # 逾期的算今天
        total_due = sum(by_day.values())
        return {
            "window_days": days,
            "total_due": total_due,
            "daily_avg": round(total_due / days, 1),
            "by_day": dict(sorted(by_day.items())),
            "overdue": sum(1 for s in states if s.is_due),
        }
