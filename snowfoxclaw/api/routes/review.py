"""SM-2 复习面 —— 把知识库里的 KU 直接当复习卡。

设计取舍:不新建「卡片表」。复习状态写进 ``KU.extra["review"]``,与 learning
子系统沿用同一套约定、同一个 ``SpacedRepetition`` 引擎。这样「录入 → 抽取 →
复习」是一条链:右栏录一句 → KDE 抽出 KU → 立刻可复习,不存在知识库一份、
学习模块又一份的双写(用户对重复存储明确反感)。

Evidence 是原文出处,单独看没有回忆价值,不进队列;其余 KU 都算卡片。
"""

import asyncio

from dataclasses import asdict
from datetime import datetime
from fastapi import HTTPException
from pydantic import BaseModel

import snowfoxclaw.api as _api
from .. import app
from snowfoxclaw.learning import ReviewState, SpacedRepetition

_SR = SpacedRepetition()

_CARD_TYPES = {"Concept", "Fact", "Claim", "Entity"}

# ReviewState.to_dict() 会额外塞 mastery / is_due,读回来时必须剔掉才能重建
_STATE_FIELDS = ("repetitions", "interval", "ease_factor", "due_date",
                 "last_review", "total_reviews", "lapses")


def _store():
    """惰性取 HKC 的 graph_store,None 表示知识库没装配好(降级成空队列)。"""
    try:
        hkc = _api._os_instance.hkc
        if hkc is None or not hkc.available:
            return None
        return hkc.container.graph_store
    except Exception:
        return None


def _has_state(ku) -> bool:
    return bool((ku.extra or {}).get("review"))


def _days_left(state) -> int:
    """按**日历日**算还剩几天,而不是 ``timedelta.days`` 向下取整。

    ``ReviewState.days_until_due`` 用的是后者:明天此刻到期会算成 0,
    于是「未来 7 天负荷」把明天的卡记进今天,而队列的 due 又是 0 —— 两个数字
    互相打架,界面上看起来像 bug。这里统一到日历日,和人对「明天」的直觉一致。
    """
    try:
        due = datetime.fromisoformat(state.due_date).date()
    except Exception:
        return 0
    return max(0, (due - datetime.now().date()).days)


def _hours_left(state) -> float:
    """小时级剩余,给「不到一天」的卡显示得更准(前端据此说「今晚」还是「明天」)。"""
    try:
        delta = datetime.fromisoformat(state.due_date) - datetime.now()
    except Exception:
        return 0.0
    return round(max(0.0, delta.total_seconds() / 3600), 1)


def _load7(learned) -> dict:
    """未来 7 天(0..6)的复习负荷,口径与队列一致。"""
    by_day = {}
    for s in learned:
        d = 0 if s.is_due else _days_left(s)
        if d <= 6:
            by_day[str(d)] = by_day.get(str(d), 0) + 1
    total = sum(by_day.values())
    return {"window_days": 7, "total_due": total,
            "daily_avg": round(total / 7, 2), "by_day": by_day,
            "overdue": sum(1 for s in learned if s.is_due)}


def _state_of(ku) -> ReviewState:
    raw = (ku.extra or {}).get("review") or {}
    return ReviewState(item_id=ku.ku_id,
                       **{k: raw[k] for k in _STATE_FIELDS if k in raw})


def _card(ku, state) -> dict:
    extra = ku.extra or {}
    text = (extra.get("source_text") or extra.get("statement")
            or extra.get("definition") or ku.summary or "")
    return {
        "ku_id": ku.ku_id,
        "name": ku.name,
        "ku_type": ku.ku_type.value,
        "domain": ku.domain or "",
        "summary": ku.summary or "",
        "text": str(text)[:1500],
        "tags": list(ku.tags or []),
        "created_at": getattr(ku, "created_at", "") or "",
        "is_new": not _has_state(ku),
        "mastery": state.mastery,
        "interval": state.interval,
        "ease_factor": state.ease_factor,
        "repetitions": state.repetitions,
        "lapses": state.lapses,
        "days_until_due": _days_left(state) if _has_state(ku) else 0,
        "due_in_hours": _hours_left(state) if _has_state(ku) else 0,
    }


def _pairs(store):
    out = []
    for ku in store.query_all(4000):
        if ku.ku_type.value not in _CARD_TYPES:
            continue
        out.append((ku, _state_of(ku)))
    return out


@app.get("/api/review/stats")
async def review_stats():
    store = _store()
    if store is None:
        return {"available": False, "cards": 0, "fresh": 0, "due": 0,
                "learned": 0, "avg_mastery": 0.0,
                "load7": {"total_due": 0, "by_day": {}, "daily_avg": 0.0, "overdue": 0},
                "risk": []}

    def _do():
        pairs = _pairs(store)
        learned = [s for ku, s in pairs if _has_state(ku)]
        due = [s for s in learned if s.is_due]
        risk = _SR.forgetting_risk(learned, 3)[:5]

        by_id = {ku.ku_id: ku.name for ku, _ in pairs}
        by_state = {s.item_id: s for s in learned}
        for r in risk:
            s = by_state.get(r["item_id"])
            r["name"] = by_id.get(r["item_id"], "")
            if s is not None:
                d = _days_left(s)
                r["days_until_due"] = d
                r["reason"] = f"掌握度仅 {s.mastery}," + ("已到期" if s.is_due else f"{d} 天内到期")

        return {
            "available": True,
            "cards": len(pairs),
            "learned": len(learned),
            "fresh": len(pairs) - len(learned),
            "due": len(due),
            "avg_mastery": round(sum(s.mastery for s in learned) / len(learned), 3) if learned else 0.0,
            "load7": _load7(learned),
            "risk": risk,
        }

    return await asyncio.to_thread(_do)


@app.get("/api/review/queue")
async def review_queue(limit: int = 20, fresh_limit: int = 10):
    """今日该复习什么。

    到期卡先上(掌握度低的优先,最弱的先补),不够 ``limit`` 再补新卡(先录的先学)。
    新卡数量单独由 ``fresh_limit`` 控制 —— 新知识全塞一天会让曲线失去意义。
    """
    store = _store()
    if store is None:
        return {"available": False, "cards": [], "total": 0}

    def _do():
        pairs = _pairs(store)
        learned = [(ku, s) for ku, s in pairs if _has_state(ku)]
        fresh = [(ku, s) for ku, s in pairs if not _has_state(ku)]

        learned.sort(key=lambda p: (p[1].mastery, p[1].days_until_due))
        fresh.sort(key=lambda p: getattr(p[0], "created_at", "") or "")

        due = [(ku, s) for ku, s in learned if s.is_due][:max(0, int(limit))]
        room = max(0, int(limit) - len(due))
        picked = due + fresh[:min(room, max(0, int(fresh_limit)))]

        return {"available": True,
                "cards": [_card(ku, s) for ku, s in picked],
                "due": len(due),
                "fresh_used": max(0, len(picked) - len(due)),
                "total": len(pairs)}

    return await asyncio.to_thread(_do)


class GradeIn(BaseModel):
    ku_id: str
    grade: int


@app.post("/api/review/grade")
async def review_grade(body: GradeIn):
    """给一张卡打回忆质量分(0 忘 / 3 勉强 / 4 记住 / 5 轻松),SM-2 推下一次间隔。"""
    store = _store()
    if store is None:
        raise HTTPException(503, "知识库未装配,复习状态无处可存")

    def _do():
        ku = store.get(body.ku_id)
        if ku is None or getattr(getattr(ku, "status", None), "value", "active") == "deleted":
            raise HTTPException(404, f"没有这条知识:{body.ku_id}")
        state = _state_of(ku)
        _SR.review(state, body.grade)
        extra = dict(ku.extra or {})
        extra["review"] = asdict(state)
        ku.extra = extra
        store.put(ku)
        return {"ok": True, "ku_id": ku.ku_id, "name": ku.name,
                "next_in_days": state.interval, "review": state.to_dict()}

    return await asyncio.to_thread(_do)
