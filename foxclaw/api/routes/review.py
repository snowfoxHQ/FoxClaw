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

import foxclaw.api as _api
from .. import app
from foxclaw.learning import ReviewState, SpacedRepetition

_SR = SpacedRepetition()

_CARD_TYPES = {"Concept", "Fact", "Claim", "Entity"}

# 扫描上限(每种卡片类型)。之前统一 query_all(4000),一是把 Evidence 这种绝对不会
# 成卡的原文出处也一起拉出来(大文档导入后它是绝大多数行),二是 4000 之后的部分
# 会被静默截断 → 复习统计少算。这里只查四种卡片类型,上限给到实际用不满的量级。
_SCAN_LIMIT = 50000

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


def _status_of(ku) -> str:
    """KU.status 是**普通字符串列**(enums 里没有 KUStatus),不是枚举。

    踩过的坑:写成 ``getattr(ku.status, "value", "active")`` 时,因为 str 没有
    ``.value`` 属性,getattr 会静默回落成默认值 "active" —— 于是「已软删的卡」也能
    通过校验被评分,复习进度写进一条已经消失的知识里。
    """
    s = getattr(ku, "status", None)
    return str(getattr(s, "value", s) or "").lower()


def _state_from(extra, ku_id: str) -> ReviewState:
    raw = (extra or {}).get("review") or {}
    return ReviewState(item_id=ku_id,
                       **{k: raw[k] for k in _STATE_FIELDS if k in raw})


def _state_of(ku) -> ReviewState:
    return _state_from(ku.extra, ku.ku_id)


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


def _card_ku_types():
    """四种卡片类型的 KUType 枚举;拿不到(未 vendor / 未加 sys.path)返回 None。"""
    try:
        from hkc_core.models.enums import KUType
        return [t for t in KUType if t.value in _CARD_TYPES]
    except Exception:
        return None


def _pairs(store):
    """所有可当复习卡的 KU + 它们当前的复习状态。

    只查四类卡片:Evidence(原文出处)单看没有回忆价值,Ability 是 ACE 编译产物,
    两者都不进队列。之前用 query_all 会把它们一起拉出来再丢掉。
    """
    types = _card_ku_types()
    if types is None:
        raws = store.query_all(_SCAN_LIMIT)
    else:
        raws = []
        for t in types:
            raws.extend(store.query_by_type(t, status="active", limit=_SCAN_LIMIT))

    out, seen = [], set()
    for ku in raws:
        if ku.ku_id in seen or _status_of(ku) != "active":
            continue
        if ku.ku_type.value not in _CARD_TYPES:
            continue
        seen.add(ku.ku_id)
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
        if ku is None or _status_of(ku) != "active":
            raise HTTPException(404, f"没有这条知识:{body.ku_id}")

        # 关键:只更新 extra 这一列,而且「读 extra → SM-2 推演 → 写回」整段在
        # graph_store 的锁里完成。走 put() 是整行 UPSERT,会把整行其它列按写入前的
        # 快照回写 —— 知识星球正在摄入时,KEE 刚合并进去的 sources / confidence /
        # supports 会被这次评分悄悄冲掉(反向亦然:复习进度被 KEE 写回覆盖)。
        # 锁内做还有个好处:连点两次评分不会两次都基于同一份旧状态推演。
        graded: list[ReviewState] = []

        def _apply(extra: dict) -> dict:
            state = _state_from(extra, body.ku_id)
            _SR.review(state, body.grade)
            extra["review"] = asdict(state)
            graded.append(state)
            return extra

        new_extra = store.update_extra(body.ku_id, _apply)
        if new_extra is None:
            raise HTTPException(404, f"没有这条知识:{body.ku_id}")
        state = graded[0]
        return {"ok": True, "ku_id": body.ku_id, "name": ku.name,
                "next_in_days": state.interval, "review": state.to_dict()}

    return await asyncio.to_thread(_do)
