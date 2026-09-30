"""
hkc-kee / dedup.py
知识级去重器 (KnowledgeDeduplicator)

KEE 作为知识身份的唯一权威,在此实现"知识级去重":
  新 KU 进入时,用 canonical fingerprint 判断库中是否已存在同一知识。
    命中 → 合并 Evidence 到已有 KU(不新建重复 KU)
    未命中 → 登记指纹,放行

与 Crystallizer 的 light fingerprint 分工:
  Crystallizer: event 级去重(避免重复消费同一事件)
  KEE(这里):   knowledge 级去重(避免同一知识因多来源而重复建 KU)

架构铁律的落点:
  "KEE is the sole authority for knowledge deduplication and evolution."
  指纹索引由 KEE 维护(不污染 GraphStore schema),冷启动时可从全量 KU 重建。
"""
from __future__ import annotations
import os
import logging
import threading
from typing import Optional, Callable

from .fingerprint import fingerprint_of_ku, canonical_fingerprint, normalize_name

logger = logging.getLogger(__name__)

# ── 语义近重(向量)配置 ──────────────────────────────────────
# 规则指纹只并"规范化后同名"的 KU;语义层再并"不同表达同一身份"的 KU
# (AI/人工智能、价值投资/价值投资法)。**仅身份型 Entity/Concept**:它们的 name 是身份标签;
# Fact/Claim 是陈述,语义归并会误并矛盾陈述("X有效"vs"X无效"余弦高但对立),故守 exact。
# 默认**关**(opt-in、behavior-preserving):归并是破坏性操作(重定向关系+软删),守 Principle 07
# 保守放量;HKC_KEE_SEMANTIC=1 开启。无 ST/模型未缓存 → 自动退回 exact-only。
_SEM_TYPES = {"entity", "concept"}


def _sem_enabled() -> bool:
    return os.environ.get("HKC_KEE_SEMANTIC", "").strip() == "1"


def _sem_model_name() -> str:
    # 与 KEE 冲突检测默认同名 → 共享同一份模型;可经环境变量换任意 ST 模型(模型无关)
    return os.environ.get("HKC_KEE_SEMANTIC_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")


def _sem_threshold() -> float:
    try:
        return float(os.environ.get("HKC_KEE_SEMANTIC_THRESHOLD", "0.90"))
    except ValueError:
        return 0.90


class KnowledgeDeduplicator:
    """
    维护 canonical_fingerprint → ku_id 索引,提供查重 + Evidence 合并。

    参数:
      graph_store: 用于查 KU、写回合并后的 KU。
      merge_evidence_fn: 可选的 Evidence 合并函数 (existing_ku, new_ku) -> bool。
                  返回 True 表示发生了合并(有新证据加入)。
                  不传则用内置默认合并(基于 supports / evidence 列表)。
    """

    def __init__(self, graph_store, merge_evidence_fn: Optional[Callable] = None):
        self.store = graph_store
        self._index: dict[str, str] = {}          # fingerprint → ku_id
        self._merge_fn = merge_evidence_fn or self._default_merge
        self._rebuilt = False
        # 去重在 knowledge.created 处理器里跑(摄入线程)。两个并发摄入任务会同时
        # check_and_merge → 竞态漏去重/半重建索引。RLock 序列化,保证"查重+登记"原子。
        self._lock = threading.RLock()
        # 语义近重索引(opt-in):(type, 规范domain) → [(ku_id, 向量)];全在 _lock 内维护
        self._sem_index: dict = {}
        self._sem_model = None
        self._sem_model_tried = False

    # ── 索引维护 ──────────────────────────────────────────────

    def rebuild_index(self) -> int:
        """从 GraphStore 全量 KU 重建指纹索引(冷启动/恢复用)。返回索引条目数。"""
        with self._lock:
            self._index.clear()
            self._sem_index.clear()
            try:
                all_kus = self.store.query_all(limit=100000)
            except Exception as e:
                logger.warning("重建指纹索引失败,查询全量 KU 出错: %s", e)
                return 0
            sem_on = _sem_enabled()
            for ku in all_kus:
                if self._is_structural_source(ku):
                    continue   # 来源层级节点(合集/文件)不进按名去重索引
                fp = fingerprint_of_ku(ku)
                # 已存在则保留最早的(先到先得),避免后来者覆盖
                self._index.setdefault(fp, ku.ku_id)
                # 语义桶(仅身份型;开启时才 embed,避免冷启动对全量 KU 编码)
                if sem_on and self._sem_type(ku) in _SEM_TYPES:
                    self._sem_register(ku)
            self._rebuilt = True
            logger.info("指纹索引重建完成,共 %d 条", len(self._index))
            return len(self._index)

    def _ensure_index(self):
        with self._lock:
            if not self._rebuilt:
                self.rebuild_index()

    # ── 语义近重(向量)──────────────────────────────────────
    @staticmethod
    def _sem_type(ku) -> str:
        t = getattr(getattr(ku, "ku_type", None), "value", "") or str(getattr(ku, "ku_type", ""))
        return t.lower()

    def _sem_model_get(self):
        """惰性取共享 ST 模型(get_st_model 内部单例);取不到(无 ST/未缓存)则永久 None。"""
        if self._sem_model_tried:
            return self._sem_model
        self._sem_model_tried = True
        try:
            from hkc_search.embedding_backends import get_st_model
            self._sem_model = get_st_model(_sem_model_name())
        except Exception:
            self._sem_model = None
        return self._sem_model

    def _embed(self, text: str):
        """走 HKC **共享向量缓存**(与检索/KEE 冲突检测同一份)。此前这里有 per-instance
        的 _sem_cache,与其它层各缓存各的;现收敛到编码咽喉,同名文本全局只编一次。"""
        if not text:
            return None
        model = self._sem_model_get()
        if model is None:
            return None
        try:
            from hkc_search.embedding_backends import encode_cached
            return encode_cached(model, _sem_model_name(), [text])[0]
        except Exception:
            return None

    def _sem_bucket_key(self, ku):
        return (self._sem_type(ku), normalize_name(getattr(ku, "domain", "") or ""))

    def _sem_name(self, ku) -> str:
        return getattr(ku, "name", "") or getattr(ku, "title", "") or ""

    def _sem_find(self, new_ku) -> Optional[str]:
        """同 type+domain 桶内找与新 KU 名字余弦≥阈值的最相近已有 KU。返回 ku_id 或 None。仅锁内调用。"""
        v = self._embed(self._sem_name(new_ku))
        if v is None:
            return None
        bucket = self._sem_index.get(self._sem_bucket_key(new_ku))
        if not bucket:
            return None
        import numpy as np
        new_id = getattr(new_ku, "ku_id", None)
        thr = _sem_threshold()
        best_id, best = None, thr
        for kid, kv in bucket:
            if kid == new_id:
                continue
            cos = float(np.dot(v, kv))
            if cos >= best:
                best, best_id = cos, kid
        return best_id

    def _sem_register(self, ku):
        """把 KU 名字向量加入语义桶(仅身份型)。仅锁内调用;取不到向量则跳过(降级 exact-only)。"""
        v = self._embed(self._sem_name(ku))
        if v is None:
            return
        self._sem_index.setdefault(self._sem_bucket_key(ku), []).append((getattr(ku, "ku_id", None), v))

    # ── 核心:查重 + 合并 ─────────────────────────────────────

    # 来源层级结构节点的标记 tag(与 hkc_kde/packager.py 约定一致):
    #   _collection      → 合集(文件夹)节点
    #   _incol:<合集id>  → 属于某合集的文件(章节)节点
    @staticmethod
    def _is_structural_source(ku) -> bool:
        """是否为"来源层级结构节点"(合集/文件 Evidence)。这类节点身份由结构决定
        (合集 find-or-create、文件按文件独立),**不参与按名去重**——否则不同文件夹里
        同名文件(README.md / 01.md 等)会被误合并,层级与 provenance 错乱。"""
        if getattr(getattr(ku, "ku_type", None), "value", "") != "Evidence":
            return False
        return any(t == "_collection" or t.startswith("_incol:")
                   for t in (getattr(ku, "tags", None) or []))

    def check_and_merge(self, new_ku) -> Optional[str]:
        """
        对新建的 KU 做知识级查重。

        返回:
          None        → 未命中(是新知识),已登记指纹,调用方应正常处理该 KU
          existing_id → 命中已有知识,已把新 KU 的 Evidence 合并进去;
                        调用方应放弃 new_ku(不再作为独立 KU 处理)
        """
        # 来源层级节点(合集/文件 Evidence)不按名去重,直接放行为独立节点
        if self._is_structural_source(new_ku):
            return None
        with self._lock:      # 整个"查重 + 登记/合并"必须原子,防并发漏去重
            self._ensure_index()
            fp = fingerprint_of_ku(new_ku)
            new_id = getattr(new_ku, "ku_id", None)

            existing_id = self._index.get(fp)

            # exact 未命中(或命中的就是自己)→ 语义近重兜底(opt-in,仅身份型)
            if existing_id is None or existing_id == new_id:
                sem_on = _sem_enabled() and self._sem_type(new_ku) in _SEM_TYPES
                sem_id = self._sem_find(new_ku) if sem_on else None
                if not sem_id or sem_id == new_id:
                    # 确无近重 → 登记指纹(+语义桶)并放行
                    self._index[fp] = new_id
                    if sem_on:
                        self._sem_register(new_ku)
                    return None
                # 语义命中 → 该新名字学成 canonical 的别名(以后走 exact 快路径),落入下面的合并
                existing_id = sem_id
                self._index[fp] = existing_id

            # 命中已有 KU → 合并 Evidence
            existing_ku = self.store.get(existing_id)
            if existing_ku is None:
                # 索引指向的 KU 已不存在(被删),用新 KU 顶替
                self._index[fp] = new_id
                return None

            try:
                merged = self._merge_fn(existing_ku, new_ku)
            except Exception as e:
                logger.warning("Evidence 合并失败 (%s ← %s): %s", existing_id, new_id, e)
                merged = False

            # 重定向重复 KU 的关系到 canonical KU(避免悬空),再软删除重复 KU
            redirected = 0
            if new_id and new_id != existing_id:
                if hasattr(self.store, "redirect_relations"):
                    try:
                        redirected = self.store.redirect_relations(new_id, existing_id)
                    except Exception as e:
                        logger.warning("关系重定向失败 (%s → %s): %s", new_id, existing_id, e)

            logger.info("知识级去重命中: new=%s 合并入 existing=%s "
                        "(有新证据=%s, 重定向关系=%d)",
                        new_id, existing_id, merged, redirected)
            return existing_id

    def register(self, ku) -> None:
        """显式登记一个 KU 的指纹(用于已知是新知识、跳过查重的场景)。"""
        with self._lock:
            self._ensure_index()
            self._index[fingerprint_of_ku(ku)] = getattr(ku, "ku_id", None)
            if _sem_enabled() and self._sem_type(ku) in _SEM_TYPES:
                self._sem_register(ku)

    # ── 默认 Evidence 合并 ────────────────────────────────────

    def _default_merge(self, existing_ku, new_ku) -> bool:
        """
        默认合并策略:把 new_ku 的来源证据并入 existing_ku。

        合并的证据载体:
          - KU 基类通用的 sources(EVD_xxx 列表,所有 KU 类型都有)
          - Claim 的 supports(支持性证据 KU id 列表)
        合并后去重,并按新增证据数微调置信度(每条 +0.02,上限 0.98;
        Fact 置信度固定 1.0 不动)。
        """
        added = 0

        # 通用 sources(所有 KU 都有)
        new_sources = getattr(new_ku, "sources", None)
        if new_sources:
            old_sources = getattr(existing_ku, "sources", None)
            if old_sources is not None:
                fresh = [s for s in new_sources if s not in old_sources]
                old_sources.extend(fresh)
                added += len(fresh)

        # Claim.supports(支持性证据)
        new_supports = getattr(new_ku, "supports", None)
        if new_supports:
            old_supports = getattr(existing_ku, "supports", None)
            if old_supports is not None:
                fresh = [s for s in new_supports if s not in old_supports]
                old_supports.extend(fresh)
                added += len(fresh)

        if added > 0:
            conf = getattr(existing_ku, "confidence", None)
            if conf is not None and not _is_fixed_confidence(existing_ku):
                existing_ku.confidence = min(0.98, conf + added * 0.02)
            self.store.put(existing_ku)

        return added > 0


def _is_fixed_confidence(ku) -> bool:
    """Fact 类型置信度固定为 1.0,不应被证据合并改动。"""
    ku_type = getattr(getattr(ku, "ku_type", None), "value", "") or str(getattr(ku, "ku_type", ""))
    return ku_type.lower() == "fact"
