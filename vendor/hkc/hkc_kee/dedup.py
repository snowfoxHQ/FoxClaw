"""
hkc-kee / dedup.py
知识级去重器 (KnowledgeDeduplicator)

KEE 作为知识身份的唯一权威,在此实现"知识级去重":
  新 KU 进入时,用 canonical identity 判断库中是否已存在同一知识。
    命中 → 合并 Evidence 到已有 KU(不新建重复 KU)
    未命中 → 登记身份键,放行

身份判定(确定性,无向量依赖):
  - 主身份 = type + 规范化 name(statement) + domain
  - 别名 = LLM 抽取的 aliases(如「北平」的别名「北京」),与主名等价
  用「别名确定性匹配」取代「向量语义近重」:零模型依赖、零余弦误合并。
  LLM 未填别名时自然退化为纯 exact 去重,行为不变。

与 Crystallizer 的 light fingerprint 分工:
  Crystallizer: event 级去重(避免重复消费同一事件)
  KEE(这里):   knowledge 级去重(避免同一知识因多来源而重复建 KU)

架构铁律的落点:
  "KEE is the sole authority for knowledge deduplication and evolution."
  身份索引由 KEE 维护(不污染 GraphStore schema),冷启动时可从全量 KU 重建。
"""
from __future__ import annotations
import logging
import threading
from typing import Optional, Callable

from .fingerprint import identity_keys, _identity_name

logger = logging.getLogger(__name__)


def _clip(s: str, n: int = 40) -> str:
    """截断长名,避免 Fact/Claim 的 statement 刷屏日志。"""
    s = s or ""
    return s if len(s) <= n else s[:n] + "…"


class KnowledgeDeduplicator:
    """
    维护 canonical identity fingerprint → ku_id 索引,提供查重 + Evidence 合并。

    参数:
      graph_store: 用于查 KU、写回合并后的 KU。
      merge_evidence_fn: 可选的 Evidence 合并函数 (existing_ku, new_ku) -> bool。
                  返回 True 表示发生了合并(有新证据加入)。
                  不传则用内置默认合并(基于 supports / evidence 列表)。
    """

    def __init__(self, graph_store, merge_evidence_fn: Optional[Callable] = None):
        self.store = graph_store
        self._index: dict[str, str] = {}          # identity fingerprint → ku_id
        self._merge_fn = merge_evidence_fn or self._default_merge
        self._rebuilt = False
        # 去重在 knowledge.created 处理器里跑(摄入线程)。两个并发摄入任务会同时
        # check_and_merge → 竞态漏去重/半重建索引。RLock 序列化,保证"查重+登记"原子。
        self._lock = threading.RLock()

    # ── 索引维护 ──────────────────────────────────────────────

    def rebuild_index(self) -> int:
        """从 GraphStore 全量 KU 重建身份索引(冷启动/恢复用)。返回索引条目数。"""
        with self._lock:
            self._index.clear()
            try:
                all_kus = self.store.query_all(limit=100000)
            except Exception as e:
                logger.warning("重建指纹索引失败,查询全量 KU 出错: %s", e)
                return 0
            for ku in all_kus:
                if self._is_structural_source(ku):
                    continue   # 来源层级节点(合集/文件)不进按名去重索引
                for k in identity_keys(ku):
                    # 已存在则保留最早的(先到先得),避免后来者覆盖
                    self._index.setdefault(k, ku.ku_id)
            self._rebuilt = True
            logger.info("身份索引重建完成,共 %d 个身份键", len(self._index))
            return len(self._index)

    def _ensure_index(self):
        with self._lock:
            if not self._rebuilt:
                self.rebuild_index()

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
          None        → 未命中(是新知识),已登记身份键,调用方应正常处理该 KU
          existing_id → 命中已有知识,已把新 KU 的 Evidence 合并进去;
                        调用方应放弃 new_ku(不再作为独立 KU 处理)
        """
        # 来源层级节点(合集/文件 Evidence)不按名去重,直接放行为独立节点
        if self._is_structural_source(new_ku):
            return None
        with self._lock:      # 整个"查重 + 登记/合并"必须原子,防并发漏去重
            self._ensure_index()
            keys = identity_keys(new_ku)
            new_id = getattr(new_ku, "ku_id", None)

            # 任一身份键(主名或别名)命中已有 KU → 判为重复
            existing_id = None
            hit_kind = "主名"        # 命中的身份键类型(主名 / 别名),供日志诊断
            for i, k in enumerate(keys):
                eid = self._index.get(k)
                if eid and eid != new_id:
                    existing_id = eid
                    hit_kind = "主名" if i == 0 else "别名"
                    break

            if existing_id is None:
                # 确无重复 → 登记所有身份键(先到先得)并放行
                for k in keys:
                    self._index.setdefault(k, new_id)
                return None

            # 命中已有 KU → 合并 Evidence
            existing_ku = self.store.get(existing_id)
            if existing_ku is None:
                # 索引指向的 KU 已不存在(被删),用新 KU 顶替
                for k in keys:
                    self._index[k] = new_id
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
                        "(键=%s, 有新证据=%s, 重定向关系=%d) new名=%r existing名=%r",
                        new_id, existing_id, hit_kind, merged, redirected,
                        _clip(_identity_name(new_ku)),
                        _clip(_identity_name(existing_ku)))
            return existing_id

    def register(self, ku) -> None:
        """显式登记一个 KU 的身份键(用于已知是新知识、跳过查重的场景)。"""
        with self._lock:
            self._ensure_index()
            kid = getattr(ku, "ku_id", None)
            for k in identity_keys(ku):
                self._index.setdefault(k, kid)

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
                # 加证据只该「抬高」置信度。原来的 min(0.98, conf+..) 在本就 1.0 的
                # KU 上会把它**降**到 0.98 —— 多一条证据反而变不确定,明显是笔误。
                existing_ku.confidence = max(conf, min(0.98, conf + added * 0.02))
            # 只写「被合并改动的那几处」,不整行回写:existing_ku 是几微秒前读出来的
            # 快照,整行 put() 会把期间别人写进 extra 的东西(如复习进度 review)按旧快照
            # 覆盖掉。这里 sources/confidence 是普通列,supports 在 extra 里。
            cols = {"sources": list(existing_ku.sources or []),
                    "confidence": existing_ku.confidence}
            ext = {}
            if new_supports:
                ext["supports"] = list(existing_ku.supports or [])
            if self.store.patch_ku(existing_ku.ku_id, columns=cols, extra=ext or None) == 0:
                self.store.put(existing_ku)   # 行已不在:退回插入语义

        return added > 0


def _is_fixed_confidence(ku) -> bool:
    """Fact 类型置信度固定为 1.0,不应被证据合并改动。"""
    ku_type = getattr(getattr(ku, "ku_type", None), "value", "") or str(getattr(ku, "ku_type", ""))
    return ku_type.lower() == "fact"
