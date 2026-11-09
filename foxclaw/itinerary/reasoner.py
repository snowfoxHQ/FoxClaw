"""
ItineraryConflictReasoner —— 行程冲突推理的认知化(v2 ③ Step4)。

把 ItineraryReasoning.detect_conflicts 的规则裁决,形式化为
    观察 → 假设 → 证据 → 裁决,
**规则永远权威**(检测到时间冲突/通勤赶不及 → confirm,绝不被 LLM 推翻);
可选 LLM 层(默认关)只对"无硬冲突、但排程紧凑"的微妙情形**升级** approve→confirm,
结构上无从推翻规则、无从隐藏已检出的冲突。裁决带证据链(可审计)。

顾问域(非治理闸口):无硬冲突时规则裁决=approve;LLM 出错→维持 approve(见基座
DomainReasoner.llm_should_escalate 的 fail-safe 语义说明)。
"""
from __future__ import annotations
from ..cognitive.reasoner import DomainReasoner, ReasonedVerdict, Evidence
from .reasoning import ItineraryReasoning

TIGHT_RATIO = 1.5


class ItineraryConflictReasoner(DomainReasoner):
    """subject = (waypoints, mode)。裁决:approve(行程可行) / confirm(有冲突或需注意)。"""

    def __init__(self, mode: str = "driving", llm=None, llm_enabled: bool = False):
        self.mode = mode
        self.llm = llm
        self.llm_enabled = llm_enabled

    def observe(self, subject) -> dict:
        waypoints, mode = subject
        timed = [w for w in waypoints if getattr(w, "dt", None) is not None]
        return {"count": len(waypoints), "timed": len(timed), "mode": mode or self.mode}

    def hypothesize(self, obs: dict) -> list:
        h = []
        if obs["timed"] >= 2:
            h += ["可能存在时间重叠", "可能通勤赶不及"]
        if obs["count"] >= 2:
            h.append("可能排程紧凑")
        return h or ["行程点不足以产生冲突"]

    def gather_evidence(self, subject, obs: dict) -> list:
        waypoints, mode = subject
        mode = obs["mode"]
        conflicts = ItineraryReasoning.detect_conflicts(waypoints, mode)
        ev = [Evidence(factor=c["type"], detail=c["reason"], weight=1.0, raw=c)
              for c in conflicts]
        if not conflicts:
            ev.append(Evidence("no_conflict", "未检测到时间冲突或通勤不可达", 0.5))
        try:
            clusters = ItineraryReasoning.cluster_by_proximity(waypoints)
            if clusters:
                ev.append(Evidence("proximity_clusters",
                                   f"{len(clusters)} 组可顺路合并:{clusters}", 0.3))
            dens = ItineraryReasoning.density(waypoints, mode)
            ev.append(Evidence("density",
                               f"{dens['waypoints']} 点/{dens['total_commute_min']:.0f} 分通勤"
                               + ("/跨城" if dens["cross_city"] else ""), 0.3))
        except Exception:
            pass
        return ev

    def arbitrate(self, subject, obs, hyps, ev) -> ReasonedVerdict:
        triggered = [e for e in ev if e.raw is not None]
        if triggered:
            return ReasonedVerdict(
                "confirm", "；".join(e.detail for e in triggered),
                ev, hyps, "rules")
        if DomainReasoner.llm_available(self.llm, self.llm_enabled) and self._is_tight(subject, obs):
            if DomainReasoner.llm_should_escalate(self.llm, *self._llm_prompt(subject, obs, ev)):
                return ReasonedVerdict("confirm", "排程紧凑,LLM 建议人工确认", ev, hyps, "llm")
        return ReasonedVerdict("approve", "行程可行:无时间冲突、通勤可达", ev, hyps, "rules")

    def _is_tight(self, subject, obs) -> bool:
        """无硬冲突,但存在某段空档紧贴所需通勤(available < TIGHT_RATIO×need)。"""
        waypoints, mode = subject
        mode = obs["mode"]
        timed = sorted([w for w in waypoints if getattr(w, "dt", None) is not None],
                       key=lambda w: w.dt)
        from datetime import timedelta
        for i in range(len(timed) - 1):
            cur, nxt = timed[i], timed[i + 1]
            if not (cur.has_coord and nxt.has_coord):
                continue
            cur_end = cur.dt + timedelta(minutes=cur.duration_min)
            gap = (nxt.dt - cur_end).total_seconds() / 60
            leg = ItineraryReasoning.estimate_leg(cur, nxt, mode)
            if 0 < gap and leg.duration_min <= gap < leg.duration_min * TIGHT_RATIO:
                return True
        return False

    def _llm_prompt(self, subject, obs, ev):
        ev_text = "\n".join(f"- {e.factor}: {e.detail}" for e in ev) or "(无)"
        system = ("你是行程助手。这份行程规则检查未发现硬冲突,但排程较紧。"
                  "判断是否仍建议用户人工确认。只回答 CONFIRM(建议确认)或 OK(无需)。")
        user = f"行程:{obs['count']} 个点,方式 {obs['mode']}。\n证据:\n{ev_text}\n\n只回答一个词:CONFIRM 或 OK。"
        return system, user
