"""知识核心(HKC)端点:同进程 DirectClient,惰性装配,缺依赖降级。

从 hestia/api.py 原样搬移(纯搬移,零行为变更)。共享 OS 实例经 `_api._os_instance`
晚绑定读取;路由用 `@app` 装饰器在 import 时注册。
"""

import asyncio

from fastapi import HTTPException
from pydantic import BaseModel

import hestia.api as _api
from .. import app


# ── 知识核心(HKC)：同进程 DirectClient,惰性装配,缺依赖降级 ─────────
# HKC = Hestia Knowledge Core:KDE 抽取 → KEE 演化 → 混合检索(BM25+向量+图)。
# 首次命中任一路由时才装配(见 HestiaOS.hkc 惰性属性);未 vendor/缺依赖时 available=False。

class KnowledgeSearchPayload(BaseModel):
    query: str
    mode: str = "hybrid"          # bm25 | vector | graph | hybrid
    top_k: int = 10
    domain: str = ""


class KnowledgeIngestPayload(BaseModel):
    text: str
    source_title: str = ""
    domain: str = ""


def _hkc_or_none():
    """惰性取 HKCService;None 表示降级(未 vendor/缺依赖/装配失败)。"""
    try:
        return _api._os_instance.hkc
    except Exception:
        return None


# 【P3-7】异常分类用 isinstance 优先:hasattr(e, "missing_skills") 是鸭子判断,
# 任何恰好带了同名属性的无关异常都会被误吞成「覆盖不足」证据 gate(真实错误被掩盖)。
# 真实类型在 vendor 的 hkc_sdk.exceptions(test_hkc.py 同源引用);缺 vendor/hkc 时
# import 失败降级 None,运行时回退 hasattr 判断,行为与旧版一致。
try:
    from hkc_sdk.exceptions import HKCInsufficientCoverage as _HKCInsuffCov
except Exception:
    _HKCInsuffCov = None


@app.post("/api/knowledge/search")
async def knowledge_search(p: KnowledgeSearchPayload):
    """混合检索知识库。HKC 未启用或检索失败时返回空 hits(与整体降级哲学一致)。"""
    hkc = _hkc_or_none()
    if hkc is None or not hkc.available:
        return {"available": False, "hits": []}
    try:
        hits = await asyncio.to_thread(
            hkc.client.search, p.query, p.mode, p.top_k, p.domain)
    except Exception as e:
        return {"available": True, "hits": [], "error": str(e)}
    return {"available": True, "hits": [
        {"ku_id": h.ku_id, "score": h.score, "name": h.name,
         "summary": h.summary, "mode": h.mode, "ku_type": getattr(h, "ku_type", "")}
        for h in hits]}


@app.post("/api/knowledge/ingest")
async def knowledge_ingest(p: KnowledgeIngestPayload):
    """摄入文本 → KDE 抽取 KU → KEE 演化(守 Principle 07)。需配置 LLM;未配则抽取降级。"""
    hkc = _hkc_or_none()
    if hkc is None or not hkc.available:
        raise HTTPException(503, "HKC 未启用(未 vendor 或缺依赖)")
    try:
        res = await asyncio.to_thread(
            hkc.client.ingest_text, p.text, p.source_title, p.domain)
        return {"ku_count": res.ku_count, "ku_ids": res.ku_ids, "counts": res.counts}
    except Exception as e:
        raise HTTPException(500, f"摄入失败(通常因未配置 LLM): {e}")


# ── HKC 融合第 2 档 Step1:ACE 能力(只读查询面,无 LLM、零成本)─────────
@app.get("/api/knowledge/abilities")
async def knowledge_abilities():
    """列出可编译的能力 key(来自 ACE skill_taxonomy)。只读、确定性、无 LLM。"""
    hkc = _hkc_or_none()
    if hkc is None or not hkc.available:
        return {"available": False, "abilities": []}
    try:
        keys = await asyncio.to_thread(hkc.client.list_abilities)
    except Exception as e:
        return {"available": True, "abilities": [], "error": str(e)}
    return {"available": True, "abilities": list(keys)}


@app.get("/api/knowledge/ability/{ability_key}")
async def knowledge_ability(ability_key: str):
    """编译一个能力,返回 coverage/skills/workflows。**证据驱动 gate**:知识覆盖不足时
    返回 `sufficient=False` + coverage/missing_skills——这不是错误,是"知识还不足以具备该能力"
    (HestiaOS 证据驱动哲学:没根据的能力不予授予)。只读、无 LLM。"""
    hkc = _hkc_or_none()
    if hkc is None or not hkc.available:
        return {"available": False}
    try:
        ab = await asyncio.to_thread(hkc.client.compile_ability, ability_key)
    except Exception as e:
        # 覆盖不足(HKCInsufficientCoverage 带 coverage/missing_skills)→ 证据 gate,非错误。
        # 【P3-7】isinstance 可用时**只**用 isinstance(精确判断:带 missing_skills 属性的
        # 无关异常不再被误吞成「覆盖不足」);import 失败(缺 vendor)才回退 hasattr 鸭子判断。
        # 文稿骨架写的是 isinstance OR hasattr 叠加,与其自身设计决策(「失败才回退」=二选一)
        # 不一致——OR 下鸭子判断永远生效,精确化形同虚设,故按设计决策实现。
        if _api._HKCInsuffCov is not None:
            is_insufficient = isinstance(e, _api._HKCInsuffCov)
        else:
            is_insufficient = hasattr(e, "missing_skills")
        if is_insufficient:
            return {"available": True, "sufficient": False,
                    "coverage": getattr(e, "coverage", {}),
                    "missing_skills": getattr(e, "missing_skills", []),
                    "reason": str(e)}
        raise HTTPException(404, f"能力编译失败(未知 key 或图错误): {e}")
    return {"available": True, "sufficient": True, "ability": {
        "ability_key": ab.ability_key, "display_name": ab.display_name,
        "domain": ab.domain, "coverage": ab.coverage, "version": ab.version,
        "skills": [{"skill_key": s.skill_key, "display_name": s.display_name,
                    "coverage": s.coverage, "concept_hits": s.concept_hits}
                   for s in ab.skills],
        "workflows": ab.workflows}}
