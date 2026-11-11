"""
hkc-kee / kee.py
Knowledge Evolution Engine

职责：
1. 监听 knowledge.created 事件
2. 新 Claim 进入时与已有 Claim 做冲突检测
3. 生成 Conflict Card，运行裁决策略
4. 更新 Claim 状态（pending → active / disputed / superseded）
"""
# TODO: Replace with proper package install (pyproject.toml) before v1 release

import threading
from datetime import datetime, timezone

from hkc_core.graph.base import GraphStore
from hkc_core.models.ku import ClaimKU, ConflictCard
from hkc_core.models.enums import (
    ClaimStatus, ConflictStatus, ResolutionStrategy, KUType
)
from hkc_core.utils.id_gen import IDGenerator
from hkc_kep.event_bus import EventBus, KEPEvents
from .dedup import KnowledgeDeduplicator


# ── 语义相似度（轻量版，不依赖 GPU）────────────────────────

import re as _re
# 否定词:ASCII 按词边界匹配(避免 bad⊂badge / no⊂know 的子串误命中),中文按子串。
_NEG_ASCII = {"not", "no", "never", "false", "bad", "harmful", "worse", "cannot"}
_NEG_CJK = {"不", "非", "无", "差", "不好", "没有", "并非", "并不", "错误"}
# 含否定字却实为正面/中性的高频复合词。单字否定(不/非/无/差)按子串匹配会把
# "无论/非常/不断/不仅"误判为否定,进而把互补陈述判成"矛盾"(实测假阳性主因)。
# 判否定前先剔除这些;真否定(无法/并非/不好/没有…)不在此列,仍被检出。
_NEG_CJK_FALSE = (
    "无论", "无数", "无比", "无穷", "无限", "无非", "无疑", "无关", "无外乎",
    "非常", "非凡",
    "不过", "不仅", "不但", "不断", "不只", "不妨", "不禁", "不由", "不时", "不外乎", "不失为",
    "差不多", "差点",
    # 描述性「不/无」:修饰名词或状态而非否定命题(不变/不可观测/不同/不管…),
    # 是最主要的假阳性来源——技术文档几乎每句都带,却与立场无关。
    "不变", "不同", "不论", "不管", "不再", "不需", "无需", "无须",
    "不可观测", "不可见", "不可控", "不可用", "不可变", "不可能",
)
_NEG_ASCII_RE = _re.compile(r"\b(" + "|".join(sorted(_NEG_ASCII, key=len, reverse=True)) + r")\b")
_NEGATION = _NEG_ASCII | _NEG_CJK   # 供 _score_keyword 的词集匹配(word-set,ASCII 本就安全)

def _encode_one(model, model_name, text):
    """单文本编码。走 HKC 的**共享向量缓存**(与检索/去重同一份、LRU 有界、锁外编码)。
    此前这里有自己的 _EMB_CACHE,各层各缓存各的、互不共享;现已收敛到编码咽喉一处。"""
    from hkc_search.embedding_backends import encode_cached
    return encode_cached(model, model_name, [text])[0]


class _SimpleEmbedder:
    """语义方向分数 score() ∈ [-1, 1]:
      > thr  → 同向(互相支持)   -thr~thr → 无关   < -thr → 反向(矛盾)
    阈值 thr 由 KnowledgeEvolutionEngine.similarity_threshold 判定(默认 0.30);
    本类只负责给分,不内嵌阈值。
    v2:优先 sentence-transformers 余弦(真语义,经 HKC 共享缓存);
    无 ST/加载失败 → 回退 v1 关键词重叠 + 否定词。接口不变。
    """
    _MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"   # 与 HKC LocalSTBackend 默认同名 → 共享一份
    # 否定翻转的两个门槛,缺一不可:
    #   NEG_FLIP_MIN_COS     —— 主题相关性(同领域才算得上对立);
    #   NEG_FLIP_MIN_OVERLAP —— 字面同命题(二元组 Jaccard)。
    # 只用余弦时,"同领域不同命题"也会被判反向(实测 1063 张伪冲突的主因);而只用字面
    # 重合又认不出换了说法的对立。两条一起过 = 讲同一件事且一方否定,才是真矛盾。
    NEG_FLIP_MIN_COS = 0.35
    NEG_FLIP_MIN_OVERLAP = 0.40

    def __init__(self, neg_flip_min_cos: float | None = None,
                 neg_flip_min_overlap: float | None = None):
        if neg_flip_min_cos is not None:
            self.NEG_FLIP_MIN_COS = float(neg_flip_min_cos)
        if neg_flip_min_overlap is not None:
            self.NEG_FLIP_MIN_OVERLAP = float(neg_flip_min_overlap)

    @staticmethod
    def _bigrams(text: str) -> set:
        t = "".join(ch for ch in (text or "").lower() if not ch.isspace())
        return {t[i:i + 2] for i in range(len(t) - 1)}

    def _same_proposition(self, text_a: str, text_b: str) -> bool:
        ga, gb = self._bigrams(text_a), self._bigrams(text_b)
        if not ga or not gb:
            return False
        return len(ga & gb) / len(ga | gb) >= self.NEG_FLIP_MIN_OVERLAP

    @staticmethod
    def _has_neg(text: str) -> bool:
        t = (text or "").lower()
        if _NEG_ASCII_RE.search(t):
            return True
        # 先剔除"伪否定"复合词(无论/非常/不断…),再看是否残留单字否定
        for w in _NEG_CJK_FALSE:
            t = t.replace(w, "")
        return any(n in t for n in _NEG_CJK)

    def score(self, text_a: str, text_b: str) -> float:
        model = None
        try:
            from hkc_search.embedding_backends import get_st_model
            model = get_st_model(self._MODEL_NAME)
        except Exception:
            model = None
        if model is not None:
            try:
                import numpy as np
                va = _encode_one(model, self._MODEL_NAME, text_a)   # 缓存,不重复编码
                vb = _encode_one(model, self._MODEL_NAME, text_b)
                cos = float(np.dot(va, vb))   # 归一化后点积=余弦 ∈ [-1,1]
                # 否定方向:同一命题一方否定 → 立场相反 → 反号(矛盾)
                if (self._has_neg(text_a) != self._has_neg(text_b)
                        and cos >= self.NEG_FLIP_MIN_COS
                        and self._same_proposition(text_a, text_b)):
                    return -abs(cos)
                return max(-1.0, min(1.0, cos))
            except Exception:
                pass
        return self._score_keyword(text_a, text_b)

    def _score_keyword(self, text_a: str, text_b: str) -> float:
        """v1 回退:关键词重叠 + 否定词(无 ST 时用)。"""
        a_words = set(text_a.lower().split())
        b_words = set(text_b.lower().split())
        a_neg = bool(a_words & _NEGATION)
        b_neg = bool(b_words & _NEGATION)
        a_topic = a_words - _NEGATION
        b_topic = b_words - _NEGATION
        overlap = len(a_topic & b_topic)
        if overlap == 0:
            return 0.0
        if a_neg != b_neg:
            return -0.5
        return min(0.8, overlap * 0.15)


# ── Claim 状态机 ─────────────────────────────────────────────

class ClaimStateMachine:
    """
    管理 Claim 的五种状态流转。

    pending    → active    : 找到支持 Evidence，confidence > 0.7
    pending    → disputed  : 对立 Claim，置信度相近
    pending    → rejected  : Evidence 明确反驳，confidence < 0.3
    active     → disputed  : 出现对立 Claim，置信度差 < 0.15
    active     → superseded: 新研究明确推翻
    disputed   → active    : 新 Evidence 倾向一方，差值 > 0.2
    """

    def transition(
        self,
        claim: ClaimKU,
        new_confidence: float,
        has_conflict: bool,
        conflict_gap: float = 0.0,
    ) -> ClaimStatus:
        current = claim.claim_status
        conf    = new_confidence

        if current == ClaimStatus.PENDING:
            if conf > 0.7 and not has_conflict:
                return ClaimStatus.ACTIVE
            if has_conflict and conflict_gap < 0.15:
                return ClaimStatus.DISPUTED
            if conf < 0.3:
                return ClaimStatus.REJECTED

        elif current == ClaimStatus.ACTIVE:
            if has_conflict and conflict_gap < 0.15:
                return ClaimStatus.DISPUTED
            if conf < 0.3:
                return ClaimStatus.SUPERSEDED

        elif current == ClaimStatus.DISPUTED:
            if conflict_gap > 0.2:
                return ClaimStatus.ACTIVE

        return current  # 无变化


# ── KEE 主引擎 ───────────────────────────────────────────────

class KnowledgeEvolutionEngine:

    def __init__(
        self,
        graph_store: GraphStore,
        event_bus:   EventBus,
        id_gen:      IDGenerator,
        similarity_threshold: float = 0.30,
        auto_resolve_gap:     float = 0.20,
        neg_flip_min_cos:     float | None = None,
        neg_flip_min_overlap: float | None = None,
    ):
        self.store   = graph_store
        self.bus     = event_bus
        self.id_gen  = id_gen
        self.embedder = _SimpleEmbedder(
            neg_flip_min_cos=neg_flip_min_cos,
            neg_flip_min_overlap=neg_flip_min_overlap,
        )
        self.state_machine = ClaimStateMachine()
        # 语义方向阈值(score∈[-1,1]):|score|>threshold 判同向(合并)/反向(冲突)。
        # 默认 0.30 与既有硬编码一致(behavior-preserving);可经构造参数调严/调宽。
        self.similarity_threshold = similarity_threshold
        self.auto_resolve_gap     = auto_resolve_gap
        self.neg_flip_min_cos     = self.embedder.NEG_FLIP_MIN_COS
        self.neg_flip_min_overlap = self.embedder.NEG_FLIP_MIN_OVERLAP

        # open 冲突卡的 (claim_a, claim_b) 对索引:替代 _handle_conflict 里每次
        # list_conflicts 全扫去重(冲突多时 O(冲突²))。并发摄入会跨线程碰它 → 加锁。
        # 惰性构建;锁获取顺序恒为「本组件锁 → graph_store 锁」,与全系统一致,无死锁。
        self._conflict_lock = threading.RLock()
        self._open_pairs = None       # None=未构建;构建后为 set[frozenset({a,b})]
        # Evidence id → "分组键"缓存。分组键 = 所属合集(文件夹)id(若 Evidence 带
        # _incol tag),否则为 Evidence 自身 id。按分组判同源:同一文件夹的多个文件
        # 视为同源、互不判矛盾(比按书名更稳,单文件/文件夹都覆盖)。幂等填充,无需锁。
        self._evd_group_cache: dict = {}

        # 注册监听
        self.bus.subscribe(KEPEvents.KNOWLEDGE_CREATED, self._on_knowledge_created)

        # 知识级去重器(KEE 是知识身份唯一权威)
        self.dedup = KnowledgeDeduplicator(graph_store)

    def reset_conflict_index(self):
        """清库后调用:失效 open 冲突对索引,下次使用时按(空)库重建。"""
        with self._conflict_lock:
            self._open_pairs = None
            self._evd_group_cache.clear()

    def _source_groups(self, claim) -> set:
        """claim 的来源分组集合。每个支持 Evidence 的"分组键":属于某合集(文件夹)
        则用合集 id(Evidence 的 _incol:<id> tag),否则用 Evidence 自身 id。
        按分组判同源——同一文件夹的多个文件(章节)视为同源、互不判矛盾。缓存。"""
        out = set()
        for eid in (getattr(claim, "supports", None) or []):
            if eid not in self._evd_group_cache:
                ku = self.store.get(eid)
                g = eid   # 默认:Evidence 自身即一个来源(单文件上传)
                if ku is not None:
                    for t in (getattr(ku, "tags", None) or []):
                        if t.startswith("_incol:"):
                            g = t[len("_incol:"):]   # 属于合集 → 用合集 id
                            break
                self._evd_group_cache[eid] = g
            out.add(self._evd_group_cache[eid])
        return out

    # ── 事件入口 ──────────────────────────────────────────────

    def _on_knowledge_created(self, event: dict):
        ku_ids = event.get("payload", {}).get("ku_ids", [])
        for ku_id in ku_ids:
            ku = self.store.get(ku_id)
            if ku is None:
                continue

            # ── 知识级去重(所有 KU 类型,先于一切处理)──
            # 命中已有同一知识 → Evidence 已合并入旧 KU → 删除这个重复新 KU,不再处理
            existing_id = self.dedup.check_and_merge(ku)
            if existing_id is not None:
                try:
                    self.store.delete(ku_id)
                except Exception:
                    pass
                continue

            # 未命中(是新知识)→ 按类型走后续演化
            if ku.ku_type == KUType.CLAIM:
                self._process_new_claim(ku)

    # ── 核心流程 ──────────────────────────────────────────────

    def _process_new_claim(self, new_claim: ClaimKU):
        """
        新 Claim 进入时的完整处理流程：
        1. 搜索同领域、**不同来源**的已有 Claim
        2. 计算语义方向
        3. 冲突 → 生成 Conflict Card → 尝试裁决
        4. 非冲突 → 融合 Evidence，更新置信度

        来源感知(Principle 07:身份/演化按证据裁决):同一来源(共享 Evidence,
        即同一本书/文档)内部的 Claim 是一份连贯材料,不互判矛盾、不互相合并证据——
        真正值得裁决的是**跨来源**的分歧。这既符合"按证据裁决"的语义,也把摄入单本
        书时最大的开销项(书内 Claim 两两比对的 N²)整块砍掉。
        """
        existing_claims = self.store.find_claims_by_domain(
            new_claim.domain or ""
        )
        new_groups = self._source_groups(new_claim)
        # 排除自身 + 排除同源(同一分组/合集;同文件夹的多个文件按合集算同源)
        existing_claims = [
            c for c in existing_claims
            if c.ku_id != new_claim.ku_id
            and (not new_groups
                 or new_groups.isdisjoint(self._source_groups(c)))
        ]

        conflict_found = False

        for old_claim in existing_claims:
            score = self.embedder.score(
                new_claim.statement,
                old_claim.statement,
            )

            if score < -self.similarity_threshold:
                # 语义方向相反 → 冲突
                conflict_found = True
                self._handle_conflict(new_claim, old_claim)

            elif score > self.similarity_threshold:
                # 同向 → 融合 Evidence，强化置信度
                self._merge_evidence(new_claim, old_claim)

        # 更新 new_claim 自身状态
        has_conflict  = conflict_found
        conflict_gap  = 0.0
        if conflict_found:
            # 找到最强对立 Claim 的置信度差
            conflict_gap = self._max_conflict_gap(new_claim, existing_claims)

        new_status = self.state_machine.transition(
            new_claim,
            new_confidence=new_claim.confidence,
            has_conflict=has_conflict,
            conflict_gap=conflict_gap,
        )

        if new_status != new_claim.claim_status:
            old_status = new_claim.claim_status
            new_claim.claim_status = new_status
            # claim_status 落在 extra 里 → 只合并这一个键,别整行回写(会冲掉并发的
            # extra 写入,如复习进度)。行已不在则退回 put 的插入语义。
            if self.store.patch_ku(
                new_claim.ku_id,
                extra={"claim_status": new_claim.claim_status.value},
            ) == 0:
                self.store.put(new_claim)

            self.bus.publish({
                "event":   KEPEvents.CLAIM_STATUS_CHANGED,
                "source":  "KEE",
                "payload": {
                    "ku_id":      new_claim.ku_id,
                    "old_status": old_status.value,
                    "new_status": new_status.value,
                }
            })
            # 阶段 B:KEE 内部状态机变化必须 publish UPDATED,否则 _sync_ku 不会重新
            # 检查该 KU,vector 里仍是旧状态(superseded/disputed 仍被检索召回)。
            self.bus.publish({
                "event":   KEPEvents.KNOWLEDGE_UPDATED,
                "source":  "KEE",
                "payload": {
                    "ku_id": new_claim.ku_id,
                    "reason": "claim_status_changed",
                }
            })

    def _ensure_open_pairs(self):
        """惰性构建 open 冲突对索引(frozenset({a,b}))。仅在持 _conflict_lock 时调用。"""
        if self._open_pairs is None:
            pairs = set()
            try:
                for card in self.store.list_conflicts(status="open"):
                    pairs.add(frozenset((card.claim_a_id, card.claim_b_id)))
            except Exception:
                pass
            self._open_pairs = pairs

    def _forget_open_pair(self, a_id: str, b_id: str):
        """冲突卡关闭后从 open 对索引移除。所有裁决路径(自动/人工)统一调用,
        保证内存索引与库中 open 卡一致——否则残留旧对会误跳过该对的新冲突。"""
        with self._conflict_lock:
            if self._open_pairs is not None:
                self._open_pairs.discard(frozenset((a_id, b_id)))

    def _handle_conflict(self, new_claim: ClaimKU, old_claim: ClaimKU):
        """生成 Conflict Card 并尝试自动裁决。"""
        pair = frozenset((new_claim.ku_id, old_claim.ku_id))
        # 用内存索引避免同一对重复建卡(旧实现每次 list_conflicts 全扫 = O(冲突²))
        with self._conflict_lock:
            self._ensure_open_pairs()
            if pair in self._open_pairs:
                return  # 已存在同一对的 open 冲突卡
            card = ConflictCard(
                conflict_id=self.id_gen.next("Conflict"),
                claim_a_id=old_claim.ku_id,
                claim_b_id=new_claim.ku_id,
                domain=new_claim.domain or old_claim.domain,
                status=ConflictStatus.OPEN,
                resolution_strategy=ResolutionStrategy.EVIDENCE_WEIGHT,
            )
            self.store.save_conflict(card)
            self._open_pairs.add(pair)

        self.bus.publish({
            "event":   KEPEvents.CONFLICT_DETECTED,
            "source":  "KEE",
            "payload": {
                "conflict_id": card.conflict_id,
                "claim_a":     old_claim.ku_id,
                "claim_b":     new_claim.ku_id,
                "domain":      card.domain,
            }
        })

        # 尝试自动裁决
        self._try_auto_resolve(card, old_claim, new_claim)

    def _try_auto_resolve(
        self,
        card: ConflictCard,
        claim_a: ClaimKU,
        claim_b: ClaimKU,
    ):
        """
        evidence_weight 策略：
        置信度差 > auto_resolve_gap (0.20) 才自动裁决。
        差距不足 → 保持 open，等待人工介入。
        """
        gap = abs(claim_a.confidence - claim_b.confidence)
        if gap < self.auto_resolve_gap:
            return  # 差距不足，不自动裁决

        winner = claim_a if claim_a.confidence >= claim_b.confidence else claim_b
        loser  = claim_b if winner.ku_id == claim_a.ku_id else claim_a

        # 更新状态(只合并 extra.claim_status,不整行回写)
        winner.claim_status = ClaimStatus.ACTIVE
        loser.claim_status  = ClaimStatus.SUPERSEDED
        for c in (winner, loser):
            if self.store.patch_ku(
                c.ku_id, extra={"claim_status": c.claim_status.value}
            ) == 0:
                self.store.put(c)
        # 阶段 B:winner(→active)/loser(→superseded)状态都变了,发 UPDATED 让 _sync_ku
        # 同步 vector:loser 必须从 vector 移除,否则检索仍召回已废弃 Claim。
        self.bus.publish({
            "event":   KEPEvents.KNOWLEDGE_UPDATED,
            "source":  "KEE",
            "payload": {
                "ku_ids": [winner.ku_id, loser.ku_id],
                "reason": "conflict_auto_resolved",
            }
        })

        # 关闭冲突卡
        card.status      = ConflictStatus.RESOLVED
        card.resolved_at = datetime.now(timezone.utc).isoformat()
        card.resolution_note = (
            f"自动裁决：{winner.ku_id} 置信度更高 "
            f"({winner.confidence:.2f} vs {loser.confidence:.2f})"
        )
        self.store.save_conflict(card)
        self._forget_open_pair(card.claim_a_id, card.claim_b_id)  # 关闭→移出 open 索引

        self.bus.publish({
            "event":   KEPEvents.CONFLICT_RESOLVED,
            "source":  "KEE",
            "payload": {
                "conflict_id": card.conflict_id,
                "winner":      winner.ku_id,
                "loser":       loser.ku_id,
            }
        })

    def _merge_evidence(self, new_claim: ClaimKU, old_claim: ClaimKU):
        """
        新 Claim 与旧 Claim 同向时：
        把新 Claim 的 Evidence 合并到旧 Claim，提升其置信度。
        """
        if not new_claim.supports:
            return
        added = [e for e in new_claim.supports if e not in old_claim.supports]
        if not added:
            return
        old_claim.supports.extend(added)
        # 每多一条 Evidence，置信度 +0.02，上限 0.98;只能是「抬高」,不能把本来
        # 1.0 的置信度因为多一条证据降到 0.98。
        old_claim.confidence = max(
            old_claim.confidence,
            min(0.98, old_claim.confidence + len(added) * 0.02),
        )
        # supports 在 extra、confidence 是普通列 → 只写这两处,别整行回写
        if self.store.patch_ku(
            old_claim.ku_id,
            columns={"confidence": old_claim.confidence},
            extra={"supports": list(old_claim.supports)},
        ) == 0:
            self.store.put(old_claim)
        # 阶段 B:置信度/supports 变了,虽然向量文本没变(text_hash 同),仍 publish UPDATED
        # 让 _sync_ku 走 (5b) 同 hash 跳过 encode,只是保证检索侧能拿到最新 confidence
        # 注:_sync_ku 五分支不返回 confidence;但显式发 UPDATED 让审计日志完整。
        self.bus.publish({
            "event":   KEPEvents.KNOWLEDGE_UPDATED,
            "source":  "KEE",
            "payload": {
                "ku_id": old_claim.ku_id,
                "reason": "evidence_merged",
            }
        })

    def _max_conflict_gap(
        self,
        claim: ClaimKU,
        others: list[ClaimKU],
    ) -> float:
        """找到与 claim 最接近（最危险）的对立 Claim 的置信度差。"""
        gaps = []
        for other in others:
            score = self.embedder.score(claim.statement, other.statement)
            if score < -self.similarity_threshold:
                gaps.append(abs(claim.confidence - other.confidence))
        return min(gaps) if gaps else 1.0

    # ── 手动裁决接口（供 API 层调用）─────────────────────────

    def manual_resolve(
        self,
        conflict_id: str,
        winner_id:   str,
        note:        str = "",
        resolved_by: str = "human",
    ) -> bool:
        card = self.store.get_conflict(conflict_id)
        if not card or card.status != ConflictStatus.OPEN:
            return False

        loser_id = (
            card.claim_b_id
            if winner_id == card.claim_a_id
            else card.claim_a_id
        )
        winner = self.store.get(winner_id)
        loser  = self.store.get(loser_id)
        if not winner or not loser:
            return False

        winner.claim_status = ClaimStatus.ACTIVE
        loser.claim_status  = ClaimStatus.SUPERSEDED
        for c in (winner, loser):
            if self.store.patch_ku(
                c.ku_id, extra={"claim_status": c.claim_status.value}
            ) == 0:
                self.store.put(c)
        # 阶段 B:同自动裁决,loser 变 superseded 必须从 vector 移除。
        self.bus.publish({
            "event":   KEPEvents.KNOWLEDGE_UPDATED,
            "source":  "KEE",
            "payload": {
                "ku_ids": [winner.ku_id, loser.ku_id],
                "reason": "conflict_manual_resolved",
            }
        })

        card.status      = ConflictStatus.RESOLVED
        card.resolved_by = resolved_by
        card.resolved_at = datetime.now(timezone.utc).isoformat()
        card.resolution_note = note or f"人工裁决：{winner_id} 胜出"
        self.store.save_conflict(card)
        self._forget_open_pair(card.claim_a_id, card.claim_b_id)  # 关闭→移出 open 索引

        self.bus.publish({
            "event":   KEPEvents.CONFLICT_RESOLVED,
            "source":  "KEE",
            "payload": {
                "conflict_id": conflict_id,
                "winner":      winner_id,
                "loser":       loser_id,
                "by":          resolved_by,
            }
        })
        return True

    def dismiss_conflict(
        self,
        conflict_id: str,
        note:        str = "",
        resolved_by: str = "human",
    ) -> bool:
        """按「误判」关闭一张冲突卡:不判胜负,双方 claim_status 保持原样。"""
        return self.dismiss_conflicts(
            [conflict_id], note=note, resolved_by=resolved_by
        ) == 1

    def dismiss_conflicts(
        self,
        conflict_ids,
        note:        str = "",
        resolved_by: str = "human",
    ) -> int:
        """批量关闭误判冲突卡,返回真正关闭的张数。

        与 manual_resolve 的差别:没有 winner,也不把任何一方标成 superseded。
        唯一例外是因冲突被置为 disputed 的 claim——disputed 会被检索层剔除
        (_sync_ku 分支 3),若它已不牵涉任何 open 冲突,这里恢复成 active;
        否则关掉卡片等于让这条知识永久隐身。
        """
        cards = []
        for cid in dict.fromkeys(conflict_ids or []):
            card = self.store.get_conflict(cid)
            if card and card.status == ConflictStatus.OPEN:
                cards.append(card)
        if not cards:
            return 0

        note = note or "误判：双方观点并存，不构成对立"
        ts   = datetime.now(timezone.utc).isoformat()
        touched = set()
        for card in cards:
            card.status          = ConflictStatus.RESOLVED
            card.resolved_by     = resolved_by
            card.resolved_at     = ts
            card.resolution_note = note
            self.store.save_conflict(card)
            self._forget_open_pair(card.claim_a_id, card.claim_b_id)
            touched.update((card.claim_a_id, card.claim_b_id))
            self.bus.publish({
                "event":   KEPEvents.CONFLICT_RESOLVED,
                "source":  "KEE",
                "payload": {
                    "conflict_id": card.conflict_id,
                    "reason":      "dismissed",
                    "by":          resolved_by,
                }
            })

        with self._conflict_lock:
            self._ensure_open_pairs()
            still_open = set(self._open_pairs or ())

        restored = []
        for ku_id in touched:
            if any(ku_id in pair for pair in still_open):
                continue
            ku = self.store.get(ku_id)
            if ku is None or getattr(ku, "claim_status", None) != ClaimStatus.DISPUTED:
                continue
            ku.claim_status = ClaimStatus.ACTIVE
            if self.store.patch_ku(
                ku_id, extra={"claim_status": ClaimStatus.ACTIVE.value}
            ) == 0:
                self.store.put(ku)
            restored.append(ku_id)

        if restored:
            self.bus.publish({
                "event":   KEPEvents.KNOWLEDGE_UPDATED,
                "source":  "KEE",
                "payload": {
                    "ku_ids": restored,
                    "reason": "conflict_dismissed_restored",
                }
            })
        return len(cards)

