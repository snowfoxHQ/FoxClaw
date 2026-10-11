"""
hkc-kde / packager.py
KU 打包器：把 ExtractionResult 转成正式 KU，写入 GraphStore，触发事件。

职责（遵循 HKC Principle 07: Single Knowledge Authority）：
1. 分配 KU ID
2. 同批 Evidence 关联 Claim（SUPPORTS，仅批内组装,非知识身份判断）
3. 批量写入 GraphStore
4. 发布 knowledge.created 事件

KDE = Producer。**不做**知识身份判断 / 合并 / 冲突 / 置信度演化 ——
这些是 KEE 的专属职责。知识级去重/合并/置信度演化全部由 KEE 的
KnowledgeDeduplicator(订阅 knowledge.created)接管;KDE 只负责产出 KU 并发事件。
"""
from __future__ import annotations
import logging
import threading

from .models import ExtractionResult
from .relation_disc import RelationshipDiscovery

logger = logging.getLogger(__name__)

# 合集(文件夹)Evidence 的标记 tag,与普通文件 Evidence 区分
COLLECTION_TAG = "_collection"
# 文件 Evidence 记录所属合集 id 的 tag 前缀
INCOL_TAG_PREFIX = "_incol:"


class KUPackager:

    def __init__(self, graph_store, event_bus, id_gen):
        self.store    = graph_store
        self.bus      = event_bus
        self.id_gen   = id_gen
        self.rel_disc = RelationshipDiscovery()
        # 合集 Evidence 的 find-or-create 必须原子(并发摄入同一文件夹的多个文件时,
        # 否则会建出多个同名合集节点)。锁序:本组件锁 → graph_store 锁,全系统一致。
        self._collection_lock = threading.Lock()

    def package(
        self,
        result: ExtractionResult,
        source_title: str = "",
        source_year:  int = 0,
        source_id:    str = "",
        collection:   str = "",
        content_hash: str = "",
    ) -> list:
        """
        主入口：把 ExtractionResult 转成 KU 列表并写入系统。
        返回写入的 KU 列表。

        collection: 文件夹名(批量摄入时传)。非空时:
          - 确保存在唯一的"合集 Evidence"(name=文件夹名,一级来源节点);
          - 本文档的文件 Evidence(二级,name=文件名)建 PART_OF 关系挂到合集,
            并打 _incol:<合集id> tag,供 KEE 按合集(而非文件)判同源。
        """
        from hkc_core.models.ku import (
            EntityKU, ConceptKU, FactKU, ClaimKU, EvidenceKU, Relation
        )
        from hkc_core.models.enums import (
            EntityType, ClaimStatus
        )

        new_kus = []
        # 需要在 Evidence 就绪后回连来源的知识 KU(③ KU←Evidence 链闭环)
        # 分两类:entity 用 MENTIONS 关系;concept/fact 填入 sources 字段。
        entities_to_link = []   # EntityKU 列表 → Evidence --MENTIONS--> Entity
        sourced_to_link  = []   # Concept/Fact KU 列表 → sources += evd_ids

        # ── 1. Entity ────────────────────────────────────────
        # 知识身份判断/去重/别名合并由 KEE 负责(Principle 07)。KDE 一律产出新 KU;
        # 若与已有 Entity 同一身份,KEE 会合并并 redirect_relations,MENTIONS 不丢。
        for item in result.all_entities():
            try:
                etype = EntityType(item.get("type", "Person"))
            except ValueError:
                etype = EntityType.PERSON

            ku = EntityKU(
                ku_id       = self.id_gen.next("Entity"),
                name        = item["name"],
                domain      = "",
                entity_type = etype,
                aliases     = item.get("aliases", []),
                source_text = item.get("_src", ""),   # 原文段落
            )
            new_kus.append(ku)
            entities_to_link.append(ku)

        # ── 2. Concept ───────────────────────────────────────
        # 去重由 KEE 负责;重复 Concept 会被 KEE 合并(含 sources 并入)后软删除。
        for item in result.all_concepts():
            ku = ConceptKU(
                ku_id       = self.id_gen.next("Concept"),
                name        = item["name"],
                domain      = item.get("domain", ""),
                definition  = item.get("definition", ""),
                summary     = item.get("definition", ""),
                source_text = item.get("_src", ""),   # 原文段落
            )
            new_kus.append(ku)
            sourced_to_link.append(ku)

        # ── 3. Fact ──────────────────────────────────────────
        for item in result.all_facts():
            stmt = item.get("statement", "").strip()
            if not stmt:
                continue
            # 去重由 KEE 负责(同 stmt 的 Fact 会被 KEE 合并后软删除)。
            ku = FactKU(
                ku_id       = self.id_gen.next("Fact"),
                name        = stmt[:80],    # 截断作为 name
                statement   = stmt,
                source_ref  = "",
                summary     = stmt,
                source_text = item.get("_src", ""),   # 原文段落
            )
            new_kus.append(ku)
            sourced_to_link.append(ku)

        # ── 4. Evidence（整份文档=一个来源=一个 Evidence）──
        # 之前"每个 chunk 一个 Evidence"会让一本书切出成百上千个同名 Evidence,
        # 依赖 KEE 去重兜底,一旦漏兜(跨摄入/并发/重启)就爆:满屏同名标签 + 线条爆炸。
        # 改为整份文档只建一个 Evidence 作为该书的来源锚点,所有 KU 都连它。
        # 方向二：按内容哈希 find-or-create —— 相同内容复用已有 Evidence，
        # 作为入口哈希查重(KDE.ingest_file)在写入层的兜底(如 ingest_text 等未拦截路径)。
        evd_kus = []
        evd_reused = False
        if any(any([raw.facts, raw.claims, raw.entities, raw.concepts]) for raw in result.items):
            src = source_id or source_title or result.source
            ku = self._find_evidence_by_hash(content_hash) if content_hash else None
            if ku is not None:
                evd_reused = True
            else:
                ku = EvidenceKU(
                    ku_id       = self.id_gen.next("Evidence"),
                    name        = source_title or source_id or result.source,
                    source      = src,                       # 精确来源标识(原始文件名等)
                    source_type = self._guess_source_type(src),
                    year        = source_year,
                    summary     = f"来自 {source_title or source_id or result.source}",
                    domain      = "",
                )
                if content_hash:
                    ku.extra["content_hash"] = content_hash
            evd_kus.append(ku)
            if not evd_reused:
                new_kus.append(ku)

        # 合集(文件夹)归属:确保合集节点存在,给文件 Evidence 打 _incol tag(须在写入前)
        collection_ku = None
        if collection and evd_kus:
            collection_ku = self._ensure_collection_evidence(collection)
            for e in evd_kus:
                tag = INCOL_TAG_PREFIX + collection_ku.ku_id
                if tag not in e.tags:
                    e.tags.append(tag)

        # evd_kus 必须先写入，Claim.supports 才不会悬空
        if evd_kus:
            self.store.batch_write(evd_kus)

        # 文件 Evidence --PART_OF--> 合集(写入后建关系,供星球/wiki 下钻)
        if collection_ku is not None:
            for e in evd_kus:
                try:
                    self.store.add_relation(Relation(
                        rel_id   = self.id_gen.next("Relation"),
                        from_ku  = e.ku_id,
                        to_ku    = collection_ku.ku_id,
                        rel_type = "PART_OF",
                    ))
                except Exception as ex:
                    logger.warning("PART_OF 关系写入失败: %s", ex)

        # ③ KU←Evidence 链闭环:把本批 Evidence 回连到 Entity/Concept/Fact。
        # 这是 KDE 作为 Producer 记录"知识产地"的本职(非知识身份判断,不违反 Principle 07)。
        evd_ids_all = [e.ku_id for e in evd_kus]
        if evd_ids_all:
            # Concept/Fact:把 Evidence id 填入 sources 字段(EVD_xxx),随第 6 步写入持久化
            for ku in sourced_to_link:
                for eid in evd_ids_all:
                    if eid not in ku.sources:
                        ku.sources.append(eid)
            # Entity 的 MENTIONS 关系在第 6 步 KU 写入后建立(见下方)

        # ── 5. Claim ─────────────────────────────────────────
        claim_kus = []
        for item in result.all_claims():
            stmt = item.get("statement", "").strip()
            if not stmt:
                continue
            # 同一 Claim 的 supports 合并 + 置信度演化属于 KEE 的知识演化职责
            # (Principle 07)。KDE 只产出带本批 Evidence 的新 Claim;若与已有 Claim
            # 同一身份,KEE 的 KnowledgeDeduplicator 会并入 supports、演化置信度后软删重复。
            ku = ClaimKU(
                ku_id        = self.id_gen.next("Claim"),
                name         = stmt[:80],
                statement    = stmt,
                confidence   = item.get("confidence", 0.5),
                domain       = item.get("domain", ""),
                claim_status = ClaimStatus.PENDING,
                supports     = [e.ku_id for e in evd_kus],
                summary      = stmt,
                source_text  = item.get("_src", ""),   # 原文段落
            )
            claim_kus.append(ku)
            new_kus.append(ku)

        if not new_kus:
            logger.info("ExtractionResult 为空，无 KU 写入")
            return []

        # ── 6. 批量写入 GraphStore（evd_kus 已提前写入，此处排除）────
        evd_ids = {e.ku_id for e in evd_kus}
        kus_to_write = [k for k in new_kus if k.ku_id not in evd_ids]
        if kus_to_write:
            self.store.batch_write(kus_to_write)
        logger.info("写入 %d 个 KU (doc=%s)", len(new_kus), result.doc_id)

        # ③ Entity 的来源链:Evidence --MENTIONS--> Entity(在两端 KU 都写入后建立)
        if evd_ids_all and entities_to_link:
            from hkc_core.models.ku import Relation
            for ent in entities_to_link:
                # 已有的 MENTIONS 来源(去重,避免重复来源重复建关系)
                existing_srcs = {
                    r.from_ku for r in self.store.get_relations(ent.ku_id, direction="in")
                    if r.rel_type == "MENTIONS"
                }
                for evd in evd_kus:
                    if evd.ku_id in existing_srcs:
                        continue
                    try:
                        self.store.add_relation(Relation(
                            rel_id   = self.id_gen.next("Relation"),
                            from_ku  = evd.ku_id,
                            to_ku    = ent.ku_id,
                            rel_type = "MENTIONS",
                            source   = evd.ku_id,
                        ))
                    except Exception as e:
                        logger.warning("Entity 来源关系写入失败: %s", e)

        # ── 7. 关系发现 ───────────────────────────────────────
        existing_kus = self._query_all_kus(limit=1000)
        rels = self.rel_disc.discover(new_kus, existing_kus, self.id_gen)
        # 批量单次落盘(逐条 add_relation 各自 commit 会在大文档时 fsync 风暴致卡死)。
        # 批量失败(如个别关系外键违约)会整批回滚 → 回退逐条,只跳过坏的那条,
        # 保住其余关系(恢复旧实现的逐条容错,同时保留正常路径的批量快速)。
        try:
            self.store.batch_add_relations(rels)
        except Exception as e:
            logger.warning("批量关系写入失败,回退逐条: %s", e)
            for rel in rels:
                try:
                    self.store.add_relation(rel)
                except Exception as e2:
                    logger.warning("关系写入失败(跳过): %s", e2)

        # ── 8. 发布事件 ───────────────────────────────────────
        from hkc_kep.event_bus import KEPEvents
        self.bus.publish({
            "event":  KEPEvents.KNOWLEDGE_CREATED,
            "source": "KDE",
            "payload": {
                "doc_id":  result.doc_id,
                "ku_ids":  [ku.ku_id for ku in new_kus],
                "counts": {
                    "entities": len([k for k in new_kus if k.ku_type.value == "Entity"]),
                    "concepts": len([k for k in new_kus if k.ku_type.value == "Concept"]),
                    "facts":    len([k for k in new_kus if k.ku_type.value == "Fact"]),
                    "claims":   len([k for k in new_kus if k.ku_type.value == "Claim"]),
                    "evidence": len([k for k in new_kus if k.ku_type.value == "Evidence"]),
                },
            }
        })

        return new_kus

    def _query_all_kus(self, limit: int = 1000) -> list:
        """全量查询所有非删除 KU，供关系发现使用。"""
        return self.store.query_all(limit=limit)

    def _find_evidence_by_hash(self, content_hash: str):
        """按内容 SHA-256 指纹查找已入库的文件 Evidence（find-or-create 的「find」）。"""
        if not content_hash:
            return None
        from hkc_core.models.enums import KUType
        for ku in self.store.query_by_type(
            KUType.EVIDENCE, status="active", limit=10_000_000
        ):
            if (getattr(ku, "extra", None) or {}).get("content_hash") == content_hash:
                return ku
        return None

    def _ensure_collection_evidence(self, collection: str):
        """找到或新建"合集 Evidence"(文件夹一级节点,name=文件夹名)。按名去重、
        每文件夹一个;并发摄入同文件夹多文件时用锁保证原子(否则会建出多个同名合集)。"""
        from hkc_core.models.ku import EvidenceKU
        with self._collection_lock:
            for ku in self.store.search_by_name(collection, fuzzy=False):
                if (getattr(getattr(ku, "ku_type", None), "value", "") == "Evidence"
                        and COLLECTION_TAG in (getattr(ku, "tags", None) or [])):
                    return ku   # 已有该文件夹的合集节点,复用
            ku = EvidenceKU(
                ku_id       = self.id_gen.next("Evidence"),
                name        = collection,
                source      = "collection:" + collection,
                source_type = self._guess_source_type("collection:" + collection),
                summary     = f"合集 · {collection}",
                domain      = "",
            )
            ku.tags = [COLLECTION_TAG]
            self.store.batch_write([ku])
            logger.info("新建合集节点: %s (%s)", collection, ku.ku_id)
            return ku

    def _guess_source_type(self, source: str) -> "SourceType":
        from hkc_core.models.enums import SourceType
        s = source.lower()
        if s.startswith("http"):
            return SourceType.WEB
        if s.endswith(".pdf"):
            return SourceType.BOOK
        if s.endswith((".md", ".txt", ".docx")):
            return SourceType.ARTICLE
        return SourceType.BOOK
