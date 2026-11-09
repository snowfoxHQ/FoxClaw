"""
HKC 事件 → 索引 C/U/D 同步测试(阶段 B 提交)。

覆盖范围:
  E1  CREATED → vector.append_ku,vector_size + 1
  E2  CREATED × N → vector_size + N,顺序保持
  E3  UPDATED 内容变(text_hash 变)→ vector.update_ku 重编,size 不变
  E4  UPDATED 内容同(text_hash 同)→ vector.update_ku 跳过 encode,size 不变
  E5  UPDATED 把 Claim 改成 superseded → vector.remove
  E6  DELETED → vector.remove
  E7  sqlite_store.delete 返 True/False(已删/不存在)
  E8  KEE _process_new_claim 状态变化后 publish UPDATED(vector 同步 remove)
  E9  KEE _try_auto_resolve 后 publish UPDATED(loser 从 vector 移除)
  E10 KEE _merge_evidence 后 publish UPDATED(text_hash 同,跳过 encode 但事件仍发)
  E11 KEE manual_resolve 后 publish UPDATED
  E12 routes.delete_source 级联删 publish DELETED(每个被删 KU 都从 vector 移除)
  E13 重复 KU 被 KEE 软删后 CREATED 处理器不再加入索引(回归用户特别要求)
  E14 _sync_ku 五分支(4)active + 不在 vector → append
  E15 _sync_ku 五分支(3)status=superseded + 在 vector → remove
  E16 批量 DELETED 事件(paylod.ku_ids 列表)→ 全部 remove

设计原则(同 P0/P1):
  - 不 mock:真 SQLite + 真 faiss + StubBackend + 真 HKCContainer + 真 KEE。
  - vendor 隔离硬阻断:模块级解析后立即自检 __file__,不是 vendor 就 RuntimeError。
  - 不依赖网络 / 真实模型;全程 tmp_path。
  - 每个测试独立 tmp_path;HKCContainer init/reset 严格 try/finally。
"""
from __future__ import annotations

import sys
from pathlib import Path


from hkc_search.hybrid import HybridSearch
from hkc_search.vector_search import VectorIndex
from hkc_search.embedding_backends import StubBackend
from hkc_api.container import HKCContainer, init_container, reset_container
from hkc_core.graph.sqlite_store import SQLiteGraphStore
from hkc_core.models.ku import ClaimKU, ConceptKU, EvidenceKU, ConflictCard
from hkc_kep.event_bus import EventBus, KEPEvents

_VENDOR_ROOT = Path(__file__).resolve().parents[1] / "vendor" / "hkc"


def _assert_vendor_path(obj, name: str) -> None:
    mod = obj.__module__
    f = getattr(sys.modules[mod], "__file__", "") or ""
    assert str(Path(f).resolve()).startswith(str(_VENDOR_ROOT.resolve())), (
        f"vendor isolation failed: {name}.__module__={mod!r} __file__={f!r} "
        f"not under vendor/hkc. Test is running against wrong HKC copy."
    )


for _cls_name, _cls in [
    ("HybridSearch",        HybridSearch),
    ("VectorIndex",         VectorIndex),
    ("StubBackend",         StubBackend),
    ("HKCContainer",        HKCContainer),
    ("SQLiteGraphStore",    SQLiteGraphStore),
    ("ConceptKU",           ConceptKU),
    ("ClaimKU",             ClaimKU),
    ("EventBus",            EventBus),
]:
    _assert_vendor_path(_cls, _cls_name)


class TestEventCreatedAppendsToIndex:
    def test_create_single_ku_appends(self, tmp_path):
        c = init_container(data_dir=str(tmp_path), embedding_kind="stub")
        try:
            ku = ConceptKU(ku_id="KU_E1", name="x", summary="x", domain="d")
            c.graph_store.put(ku)
            from hkc_kep.event_bus import KEPEvents
            c.event_bus.publish({
                "event":   KEPEvents.KNOWLEDGE_CREATED,
                "source":  "test",
                "payload": {"ku_ids": ["KU_E1"]},
            })
            assert c.search.vector.size() == 1
            assert "KU_E1" in c.search.vector._ku_ids
        finally:
            c.close()
            reset_container()

    def test_create_many_kus_preserves_order(self, tmp_path):
        c = init_container(data_dir=str(tmp_path), embedding_kind="stub")
        try:
            for i in range(5):
                ku = ConceptKU(ku_id=f"KU_E2_{i}", name=f"k{i}", summary="k", domain="d")
                c.graph_store.put(ku)
            from hkc_kep.event_bus import KEPEvents
            c.event_bus.publish({
                "event":   KEPEvents.KNOWLEDGE_CREATED,
                "source":  "test",
                "payload": {"ku_ids": [f"KU_E2_{i}" for i in range(5)]},
            })
            assert c.search.vector.size() == 5
            assert c.search.vector._ku_ids == [f"KU_E2_{i}" for i in range(5)]
        finally:
            c.close()
            reset_container()


class TestEventUpdatedEncodesOrSkips:
    def test_updated_changes_text_hash_reencodes(self, tmp_path):
        """内容变(text_hash 变)→ 重编码,vector_size 不变。"""
        c = init_container(data_dir=str(tmp_path), embedding_kind="stub")
        try:
            ku = ConceptKU(ku_id="KU_E3", name="x", summary="x", domain="d")
            c.graph_store.put(ku)
            c.event_bus.publish({
                "event":   KEPEvents.KNOWLEDGE_CREATED,
                "source":  "test",
                "payload": {"ku_ids": ["KU_E3"]},
            })
            assert c.search.vector.size() == 1
            old_hash = list(c.search.vector._text_hashes)
            ku_new = ConceptKU(ku_id="KU_E3", name="x", summary="x 的描述升级了", domain="d")
            c.graph_store.put(ku_new)
            c.event_bus.publish({
                "event":   KEPEvents.KNOWLEDGE_UPDATED,
                "source":  "test",
                "payload": {"ku_ids": ["KU_E3"]},
            })
            assert c.search.vector.size() == 1
            new_hash = c.search.vector._text_hashes[0]
            assert new_hash != old_hash[0], "应换新 hash"
        finally:
            c.close()
            reset_container()

    def test_updated_same_text_hash_skips_encode(self, tmp_path):
        """内容同(text_hash 同)→ 跳过 encode,但 vector_size 不变。"""
        c = init_container(data_dir=str(tmp_path), embedding_kind="stub")
        try:
            ku = ConceptKU(ku_id="KU_E4", name="x", summary="x", domain="d")
            c.graph_store.put(ku)
            c.event_bus.publish({
                "event":   KEPEvents.KNOWLEDGE_CREATED,
                "source":  "test",
                "payload": {"ku_ids": ["KU_E4"]},
            })
            old_hash = list(c.search.vector._text_hashes)
            ku_same = ConceptKU(ku_id="KU_E4", name="x", summary="x", domain="d")
            c.graph_store.put(ku_same)
            c.event_bus.publish({
                "event":   KEPEvents.KNOWLEDGE_UPDATED,
                "source":  "test",
                "payload": {"ku_ids": ["KU_E4"]},
            })
            assert c.search.vector.size() == 1
            assert c.search.vector._text_hashes[0] == old_hash[0]
        finally:
            c.close()
            reset_container()


class TestEventUpdatedStatusChangeRemoves:
    def test_claim_superseded_removes_from_vector(self, tmp_path):
        """Claim status 改成 superseded → vector 移除(检索不该召回已废弃)。"""
        c = init_container(data_dir=str(tmp_path), embedding_kind="stub")
        try:
            from hkc_core.models.enums import ClaimStatus
            ku = ClaimKU(ku_id="KU_E5", name="claim_x", statement="x is y",
                         domain="d", claim_status=ClaimStatus.ACTIVE, confidence=0.8)
            c.graph_store.put(ku)
            c.event_bus.publish({
                "event":   KEPEvents.KNOWLEDGE_CREATED,
                "source":  "test",
                "payload": {"ku_ids": ["KU_E5"]},
            })
            assert "KU_E5" in c.search.vector._ku_ids
            ku.claim_status = ClaimStatus.SUPERSEDED
            c.graph_store.put(ku)
            c.event_bus.publish({
                "event":   KEPEvents.KNOWLEDGE_UPDATED,
                "source":  "test",
                "payload": {"ku_ids": ["KU_E5"]},
            })
            assert "KU_E5" not in c.search.vector._ku_ids
            assert c.search.vector.size() == 0
        finally:
            c.close()
            reset_container()


class TestEventDeletedRemoves:
    def test_deleted_event_removes_from_vector(self, tmp_path):
        c = init_container(data_dir=str(tmp_path), embedding_kind="stub")
        try:
            ku = ConceptKU(ku_id="KU_E6", name="x", summary="x", domain="d")
            c.graph_store.put(ku)
            c.event_bus.publish({
                "event":   KEPEvents.KNOWLEDGE_CREATED,
                "source":  "test",
                "payload": {"ku_ids": ["KU_E6"]},
            })
            assert c.search.vector.size() == 1
            assert c.graph_store.delete("KU_E6") is True
            c.event_bus.publish({
                "event":   KEPEvents.KNOWLEDGE_DELETED,
                "source":  "test",
                "payload": {"ku_id": "KU_E6"},
            })
            assert c.search.vector.size() == 0
        finally:
            c.close()
            reset_container()


class TestSQLiteStoreDeleteReturnsBool:
    def test_delete_existing_returns_true(self, tmp_path):
        store = SQLiteGraphStore(str(tmp_path / "hkc.db"))
        try:
            ku = ConceptKU(ku_id="KU_DEL_1", name="x", summary="x", domain="d")
            store.put(ku)
            assert store.delete("KU_DEL_1") is True
        finally:
            store.close()

    def test_delete_missing_returns_false(self, tmp_path):
        store = SQLiteGraphStore(str(tmp_path / "hkc.db"))
        try:
            assert store.delete("KU_DOES_NOT_EXIST") is False
        finally:
            store.close()

    def test_double_delete_returns_false_second_time(self, tmp_path):
        """幂等:再删同一个 ku_id 必返 False(已 deleted)。"""
        store = SQLiteGraphStore(str(tmp_path / "hkc.db"))
        try:
            ku = ConceptKU(ku_id="KU_DEL_2", name="x", summary="x", domain="d")
            store.put(ku)
            assert store.delete("KU_DEL_2") is True
            assert store.delete("KU_DEL_2") is False
        finally:
            store.close()


class TestKEEPublishesUpdated:
    def test_process_new_claim_status_change_publishes_updated(self, tmp_path):
        """E8:_process_new_claim 状态机变化后 publish UPDATED。"""
        c = init_container(data_dir=str(tmp_path), embedding_kind="stub")
        try:
            from hkc_core.models.enums import ClaimStatus
            ku = ClaimKU(ku_id="KU_E8", name="c", statement="some claim",
                         domain="d", claim_status=ClaimStatus.PENDING, confidence=0.1)
            c.graph_store.put(ku)
            captured: list[dict] = []
            c.event_bus.subscribe(KEPEvents.KNOWLEDGE_UPDATED,
                                   lambda e: captured.append(e))
            c.kee._process_new_claim(ku)
            assert any(
                e["payload"].get("ku_id") == "KU_E8" or
                "KU_E8" in e["payload"].get("ku_ids", [])
                for e in captured
            ), f"未捕获 KU_E8 的 UPDATED 事件: {captured}"
        finally:
            c.close()
            reset_container()

    def test_auto_resolve_publishes_updated(self, tmp_path):
        """E9:_try_auto_resolve 裁决后 publish UPDATED(loser → superseded 从 vector 移除)。
        构造:两个 statement 不同的 Claim(confidence gap=0.5 > 0.20 阈值) + OPEN conflict 卡
        → 调 _try_auto_resolve → 必走 put winner + put loser + publish UPDATED。
        注:用 _try_auto_resolve 直接调而非 _process_new_claim,避免被 dedup 路径干扰
        (两个 statement 不同时,同领域不会 dedup;但 _process_new_claim 还要走 state
        machine,可能不触发 _merge_evidence → 不发 UPDATED)。"""
        c = init_container(data_dir=str(tmp_path), embedding_kind="stub")
        try:
            from hkc_core.models.enums import ClaimStatus, ConflictStatus, ResolutionStrategy
            a = ClaimKU(ku_id="KU_E9A", name="a",
                        statement="the sky is blue today always",
                        domain="weather", claim_status=ClaimStatus.ACTIVE,
                        confidence=0.95)
            b = ClaimKU(ku_id="KU_E9B", name="b",
                        statement="the sky is gray overcast every day",
                        domain="weather", claim_status=ClaimStatus.ACTIVE,
                        confidence=0.45)
            c.graph_store.put(a)
            c.graph_store.put(b)
            card = ConflictCard(
                conflict_id="CONF_E9",
                claim_a_id="KU_E9A",
                claim_b_id="KU_E9B",
                domain="weather",
                status=ConflictStatus.OPEN,
                resolution_strategy=ResolutionStrategy.EVIDENCE_WEIGHT,
            )
            c.graph_store.save_conflict(card)
            captured: list[dict] = []
            c.event_bus.subscribe(KEPEvents.KNOWLEDGE_UPDATED,
                                   lambda e: captured.append(e))
            c.kee._try_auto_resolve(card, a, b)
            updated_payloads = [
                e["payload"] for e in captured
                if e.get("event") == KEPEvents.KNOWLEDGE_UPDATED
            ]
            assert len(updated_payloads) >= 1, f"应至少 1 条 UPDATED: {captured}"
            assert any(
                set(e.get("ku_ids", [])) >= {"KU_E9A", "KU_E9B"}
                for e in updated_payloads
            ), f"应有 a+b 的 UPDATED: {updated_payloads}"
        finally:
            c.close()
            reset_container()

    def test_merge_evidence_publishes_updated(self, tmp_path):
        """E10:_merge_evidence 后 publish UPDATED(同 hash,但事件仍发)。"""
        c = init_container(data_dir=str(tmp_path), embedding_kind="stub")
        try:
            from hkc_core.models.enums import ClaimStatus
            a = ClaimKU(ku_id="KU_E10A", name="a", statement="moon is rock",
                        domain="astro", claim_status=ClaimStatus.ACTIVE, confidence=0.6)
            b = ClaimKU(ku_id="KU_E10B", name="b", statement="moon is rock",
                        domain="astro", claim_status=ClaimStatus.ACTIVE, confidence=0.6)
            a.supports = ["EVD_1"]
            b.supports = ["EVD_2"]
            c.graph_store.put(a)
            c.graph_store.put(b)
            c.event_bus.publish({
                "event": KEPEvents.KNOWLEDGE_CREATED, "source": "test",
                "payload": {"ku_ids": ["KU_E10A", "KU_E10B"]},
            })
            captured: list[dict] = []
            c.event_bus.subscribe(KEPEvents.KNOWLEDGE_UPDATED,
                                   lambda e: captured.append(e))
            c.kee._merge_evidence(b, a)
            assert any(
                e["payload"].get("ku_id") == "KU_E10A" or
                "KU_E10A" in e["payload"].get("ku_ids", [])
                for e in captured if e.get("event") == KEPEvents.KNOWLEDGE_UPDATED
            ), f"_merge_evidence 应 publish UPDATED: {captured}"
        finally:
            c.close()
            reset_container()

    def test_manual_resolve_publishes_updated(self, tmp_path):
        """E11:manual_resolve 后 publish UPDATED。"""
        c = init_container(data_dir=str(tmp_path), embedding_kind="stub")
        try:
            from hkc_core.models.enums import ClaimStatus, ConflictStatus, ResolutionStrategy
            a = ClaimKU(ku_id="KU_E11A", name="a", statement="the sea is calm today",
                        domain="sea", claim_status=ClaimStatus.ACTIVE, confidence=0.9)
            b = ClaimKU(ku_id="KU_E11B", name="b", statement="the sea is rough today",
                        domain="sea", claim_status=ClaimStatus.ACTIVE, confidence=0.5)
            c.graph_store.put(a)
            c.graph_store.put(b)
            card = ConflictCard(
                conflict_id="CONF_E11",
                claim_a_id="KU_E11A",
                claim_b_id="KU_E11B",
                domain="sea",
                status=ConflictStatus.OPEN,
                resolution_strategy=ResolutionStrategy.MANUAL,
            )
            c.graph_store.save_conflict(card)
            captured: list[dict] = []
            c.event_bus.subscribe(KEPEvents.KNOWLEDGE_UPDATED,
                                   lambda e: captured.append(e))
            ok = c.kee.manual_resolve("CONF_E11", "KU_E11A", note="test")
            assert ok is True
            updated_payloads = [
                e["payload"] for e in captured
                if e.get("event") == KEPEvents.KNOWLEDGE_UPDATED
            ]
            assert any(
                "KU_E11B" in e.get("ku_ids", []) or
                e.get("ku_id") == "KU_E11B"
                for e in updated_payloads
            ), f"manual_resolve 应 publish UPDATED 涉及 KU_E11B: {updated_payloads}"
        finally:
            c.close()
            reset_container()


class TestDeleteSourcePublishesDeleted:
    def test_cascade_delete_publishes_deleted(self, tmp_path):
        """routes.delete_source 级联删应发 DELETED,vector 同步 remove。
        _delete_source_cascade 按 EvidenceKU.source 匹配,所以喂 EvidenceKU。"""
        c = init_container(data_dir=str(tmp_path), embedding_kind="stub")
        try:
            evd = EvidenceKU(ku_id="EVD_E12", name="e", source="book1.md",
                             quote="一段引用文本占位")
            c.graph_store.put(evd)
            c.event_bus.publish({
                "event": KEPEvents.KNOWLEDGE_CREATED, "source": "test",
                "payload": {"ku_ids": ["EVD_E12"]},
            })
            assert c.search.vector.size() == 1
            from hkc_api.routes.knowledge import _delete_source_cascade
            _delete_source_cascade(c, "book1.md")
            assert c.search.vector.size() == 0, "vector 应被同步清空"
        finally:
            c.close()
            reset_container()


class TestDedupedKUDoesNotEnterIndex:
    def test_deduped_duplicate_ku_skipped_in_index(self, tmp_path):
        """用户原话:重复 KU 被 KEE 软删后,CREATED 处理器不会重新加入索引。
        这里用 ConceptKU + 相同 name(主身份键)触发 dedup。"""
        c = init_container(data_dir=str(tmp_path), embedding_kind="stub")
        try:
            a = ConceptKU(ku_id="KU_E13A", name="重复名", summary="第一个",
                          domain="d")
            c.graph_store.put(a)
            c.event_bus.publish({
                "event": KEPEvents.KNOWLEDGE_CREATED, "source": "test",
                "payload": {"ku_ids": ["KU_E13A"]},
            })
            assert c.search.vector.size() == 1
            b = ConceptKU(ku_id="KU_E13B", name="重复名", summary="第二个",
                          domain="d")
            c.graph_store.put(b)
            c.event_bus.publish({
                "event": KEPEvents.KNOWLEDGE_CREATED, "source": "test",
                "payload": {"ku_ids": ["KU_E13B"]},
            })
            assert "KU_E13B" not in c.search.vector._ku_ids
            assert c.search.vector.size() == 1
        finally:
            c.close()
            reset_container()


class TestSyncKUBranches:
    def test_sync_ku_append_when_not_in_vector(self, tmp_path):
        """E14:五分支 (4) active + 不在 vector → append。"""
        c = init_container(data_dir=str(tmp_path), embedding_kind="stub")
        try:
            ku = ConceptKU(ku_id="KU_E14", name="x", summary="x", domain="d")
            c.graph_store.put(ku)
            c._sync_ku("KU_E14")
            assert c.search.vector.size() == 1
            assert "KU_E14" in c.search.vector._ku_ids
        finally:
            c.close()
            reset_container()

    def test_sync_ku_remove_when_superseded(self, tmp_path):
        """E15:五分支 (3) status=superseded + 在 vector → remove。"""
        c = init_container(data_dir=str(tmp_path), embedding_kind="stub")
        try:
            from hkc_core.models.enums import ClaimStatus
            ku = ClaimKU(ku_id="KU_E15", name="c", statement="x",
                         domain="d", claim_status=ClaimStatus.ACTIVE, confidence=0.8)
            c.graph_store.put(ku)
            c._sync_ku("KU_E15")
            assert c.search.vector.size() == 1
            ku.claim_status = ClaimStatus.SUPERSEDED
            c.graph_store.put(ku)
            c._sync_ku("KU_E15")
            assert c.search.vector.size() == 0
        finally:
            c.close()
            reset_container()


class TestBatchDeletedEvent:
    def test_bulk_delete_event_removes_all(self, tmp_path):
        c = init_container(data_dir=str(tmp_path), embedding_kind="stub")
        try:
            for i in range(5):
                ku = ConceptKU(ku_id=f"KU_E16_{i}", name=f"k{i}",
                               summary="k", domain="d")
                c.graph_store.put(ku)
            c.event_bus.publish({
                "event": KEPEvents.KNOWLEDGE_CREATED, "source": "test",
                "payload": {"ku_ids": [f"KU_E16_{i}" for i in range(5)]},
            })
            assert c.search.vector.size() == 5
            for i in range(5):
                assert c.graph_store.delete(f"KU_E16_{i}") is True
            c.event_bus.publish({
                "event": KEPEvents.KNOWLEDGE_DELETED, "source": "test",
                "payload": {"ku_ids": [f"KU_E16_{i}" for i in range(5)]},
            })
            assert c.search.vector.size() == 0
        finally:
            c.close()
            reset_container()
