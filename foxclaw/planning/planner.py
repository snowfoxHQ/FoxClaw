"""
统一规划原语（P1-2:三个 Planner 合并为一个引擎）。

审计结论:主管路由(→子Agent)、AHS(→skill)、search QueryPlanner(→数据源)三处
各自重复实现了同一套机制——否定词检测、标点拆分、marker 常量、触发词打分。
本模块把这些机制收敛为**单一真源**,三处保留各自的公共入口与候选空间,内部委托到这里。

不假设三者路由到同一目标:它们的候选集不同(agent/skill/source),但"怎么从文本里
匹配、拆分、打分选候选"是同一套逻辑,这一套只应有一处定义。
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

# ── 单一真源:否定词 + 顺序/并行/列表 标记 ──────────────────────
NEGATIONS = ["不", "别", "无需", "不要", "取消", "甭", "don't", "no need", "without"]
# 顺序拆分信号(明确的顺序连接词;不用"和/并"等高风险常用字,避免误拆名词短语)
SPLIT_MARKERS = ["然后", "接着", "之后", "再", "最后", "；", ";"]
# 并行信号:出现这些"同时性"连接词 → 子任务彼此无依赖,可并行 fork
PARALLEL_TRIGGERS = ["同时", "分别", "并行", "一并"]
# 列表分隔符(仅在检出并行触发词时才当拆分点,避免误拆普通名词短语)
LIST_SEPS = ["、", "，", ",", "；", ";"]


def is_negated(task: str, trigger: str, window: int = 4) -> bool:
    """触发词在任务中是否被否定词紧邻(前 window 字)修饰。"""
    idx = (task or "").find(trigger)
    if idx < 0:
        return False
    prefix = task[max(0, idx - window):idx]
    return any(neg in prefix for neg in NEGATIONS)


def split_on(task: str, markers: list) -> list:
    """按 markers 把文本切成非空片段(去空白)。"""
    pat = "|".join(map(re.escape, markers))
    return [p.strip() for p in re.split(pat, task or "") if p.strip()]


def keyword_hits(task: str, keywords: list, negation: bool = False) -> int:
    """命中的关键词数(negation=True 时排除被否定词修饰的命中)。
    主管路由用 negation=False(保持原计数行为);AHS 触发打分用 negation=True。"""
    task = task or ""
    return sum(1 for kw in keywords
               if kw in task and not (negation and is_negated(task, kw)))


@dataclass
class ScoredCandidate:
    """打分候选:id + 触发词 + 描述(领域匹配)。三处把各自候选适配成它。"""
    id: str
    triggers: list = field(default_factory=list)
    description: str = ""


@dataclass
class TriggerRoute:
    primary: str = None
    confidence: float = 0.0
    candidates: list = field(default_factory=list)   # [(id, score)]
    fallback: str = None
    needs_composition: bool = False
    reason: str = ""


def trigger_route(task: str, candidates: list, fallback_id: str | None = None,
                  min_conf: float = 0.15, near_gap: float = 0.1) -> TriggerRoute:
    """触发词路由三步打分:否定过滤后的触发命中(0.8)+ 描述领域匹配(0.2),确定性排序。
    这是 AHS 原 route 的算法,现作为三处共用的通用引擎。"""
    if not candidates:
        return TriggerRoute(reason="候选为空")

    scored = []
    for c in candidates:
        triggers = c.triggers or []
        if not triggers:
            scored.append((c.id, 0.0)); continue
        # 第1步:否定过滤后的触发命中数 → 命中即给强基础分,不被触发词总数稀释
        hits = keyword_hits(task, triggers, negation=True)
        hit_score = 0.0 if hits == 0 else min(0.6 + 0.15 * (hits - 1), 1.0)
        # 第2步:领域匹配(描述里的词出现在任务中,粗略语义近似)
        desc_words = [w for w in re.split(r"[\s,，、]", c.description or "") if len(w) >= 2]
        domain_hit = sum(1 for w in desc_words if w in task)
        domain_score = min(domain_hit / max(len(desc_words), 1), 1.0)
        score = 0.8 * hit_score + 0.2 * domain_score
        scored.append((c.id, round(score, 3)))

    scored.sort(key=lambda x: (-x[1], x[0]))   # 分降序,平局按 id 字典序(确定性)
    top_id, top_score = scored[0]
    cands = [(i, s) for i, s in scored if s > 0]

    fb = fallback_id or candidates[-1].id
    if top_score < min_conf:
        return TriggerRoute(primary=fb, confidence=top_score, candidates=cands,
                            fallback=fb, reason="无显著匹配,使用兜底")
    near = [c for c in cands if abs(c[1] - top_score) < near_gap]
    return TriggerRoute(primary=top_id, confidence=top_score, candidates=cands,
                        fallback=fb, needs_composition=len(near) > 1,
                        reason=f"主候选 {top_id}(分 {top_score})")
