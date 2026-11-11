"""误判冲突关闭(dismiss)与否定判定收紧的回归测试。

背景:技术文档里「不变/不可观测/不同」这类描述性「不」被当成立场否定,只要两句
同领域(cos>0.35)就判「矛盾」,实测攒下 1063 张伪冲突卡。dismiss 用于把这些卡关掉
且**不废弃任何一方观点**;这里锁住三件事:
  1. dismiss 后双方 claim_status 不变(尤其不能被标 superseded);
  2. 因冲突被置 disputed 的一方,在没有其它 open 冲突时恢复 active(否则检索永久丢);
  3. 仍牵涉其它 open 冲突的 claim 保持 disputed。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "vendor" / "hkc"))

from hkc_core.graph.sqlite_store import SQLiteGraphStore
from hkc_core.models.enums import ClaimStatus, ConflictStatus
from hkc_core.models.ku import ClaimKU, ConflictCard
from hkc_core.utils.id_gen import IDGenerator
from hkc_kee.kee import KnowledgeEvolutionEngine, _SimpleEmbedder
from hkc_kep.event_bus import EventBus


def _engine(tmp_path):
    store = SQLiteGraphStore(str(tmp_path / "hkc.db"))
    bus = EventBus(str(tmp_path / "events.db"))
    gen = IDGenerator(str(tmp_path / "ids.db"))
    return store, KnowledgeEvolutionEngine(store, bus, gen)


def _claim(store, ku_id, status=ClaimStatus.ACTIVE, conf=0.8):
    store.put(ClaimKU(
        ku_id=ku_id, name=ku_id, statement=f"{ku_id} 的观点",
        confidence=conf, domain="测试", claim_status=status,
    ))


def test_dismiss_keeps_both_claims_and_restores_disputed(tmp_path):
    store, kee = _engine(tmp_path)
    _claim(store, "CLM_A")
    _claim(store, "CLM_B", status=ClaimStatus.DISPUTED)
    store.save_conflict(ConflictCard(
        conflict_id="CFT_1", claim_a_id="CLM_A", claim_b_id="CLM_B", domain="测试"))

    assert kee.dismiss_conflict("CFT_1", note="误判") is True

    card = store.get_conflict("CFT_1")
    assert card.status == ConflictStatus.RESOLVED
    assert card.resolved_by == "human"
    assert card.resolution_note == "误判"
    assert card.resolved_at
    assert store.get("CLM_A").claim_status == ClaimStatus.ACTIVE
    assert store.get("CLM_B").claim_status == ClaimStatus.ACTIVE, \
        "disputed 且无其它 open 冲突 → 必须恢复 active,否则检索永久剔除"


def test_dismiss_leaves_claim_still_disputed_if_other_conflict_open(tmp_path):
    store, kee = _engine(tmp_path)
    _claim(store, "CLM_A")
    _claim(store, "CLM_B", status=ClaimStatus.DISPUTED)
    _claim(store, "CLM_C")
    store.save_conflict(ConflictCard(
        conflict_id="CFT_1", claim_a_id="CLM_A", claim_b_id="CLM_B", domain="测试"))
    store.save_conflict(ConflictCard(
        conflict_id="CFT_2", claim_a_id="CLM_B", claim_b_id="CLM_C", domain="测试"))

    assert kee.dismiss_conflict("CFT_1") is True
    assert store.get("CLM_B").claim_status == ClaimStatus.DISPUTED, \
        "还牵涉 CFT_2,不能提前恢复"
    assert store.get_conflict("CFT_2").status == ConflictStatus.OPEN


def test_dismiss_batch_and_idempotent(tmp_path):
    store, kee = _engine(tmp_path)
    _claim(store, "CLM_A")
    _claim(store, "CLM_B")
    store.save_conflict(ConflictCard(
        conflict_id="CFT_1", claim_a_id="CLM_A", claim_b_id="CLM_B"))
    store.save_conflict(ConflictCard(
        conflict_id="CFT_2", claim_a_id="CLM_A", claim_b_id="CLM_B"))

    assert kee.dismiss_conflicts(["CFT_1", "CFT_2", "CFT_2"]) == 2
    assert kee.dismiss_conflicts(["CFT_1"]) == 0, "重复关闭应为 0 而非异常"
    assert kee.dismiss_conflict("CFT_NOPE") is False


def test_has_neg_ignores_descriptive_bu():
    assert _SimpleEmbedder._has_neg("现有测试行为不变") is False
    assert _SimpleEmbedder._has_neg("Director 不可观测") is False
    assert _SimpleEmbedder._has_neg("这是「不管调度」原则的延续") is False
    assert _SimpleEmbedder._has_neg("不同模块用不同 mock") is False
    assert _SimpleEmbedder._has_neg("无论怎么改都不影响") is True, "无论剔除后残留的「不」仍算否定"
    assert _SimpleEmbedder._has_neg("这个方案不好") is True
    assert _SimpleEmbedder._has_neg("not good") is True


def test_neg_flip_defaults_are_configurable(tmp_path):
    store, _ = _engine(tmp_path)
    emb = _SimpleEmbedder()
    assert emb.NEG_FLIP_MIN_COS == 0.35
    assert emb.NEG_FLIP_MIN_OVERLAP == 0.40
    tight = _SimpleEmbedder(neg_flip_min_cos=0.9, neg_flip_min_overlap=0.8)
    assert (tight.NEG_FLIP_MIN_COS, tight.NEG_FLIP_MIN_OVERLAP) == (0.9, 0.8)
    engine = KnowledgeEvolutionEngine(
        store, EventBus(":memory:"), IDGenerator(str(tmp_path / "ids2.db")),
        neg_flip_min_cos=0.6, neg_flip_min_overlap=0.7)
    assert (engine.neg_flip_min_cos, engine.neg_flip_min_overlap) == (0.6, 0.7)


def test_same_proposition_requires_lexical_overlap():
    emb = _SimpleEmbedder()
    assert emb._same_proposition("这个方案是可行的，风险可控", "这个方案不可行，风险不可控") is True
    assert emb._same_proposition(
        "现有编译测试的输入都是高置信度正例，预期 open_questions 为空，行为不变。",
        "依赖注入优于 spy-per-module 式模块级 mock") is False


def test_score_ignores_same_domain_different_propositions():
    from hkc_search.embedding_backends import get_st_model
    if get_st_model(_SimpleEmbedder._MODEL_NAME) is None:
        import pytest
        pytest.skip("语义模型未缓存,跳过 score 级验证")

    emb = _SimpleEmbedder()
    near = ("现有编译测试的输入都是高置信度正例，预期 open_questions 为空，行为不变。",
            "依赖注入优于 spy-per-module 式模块级 mock")
    assert emb.score(*near) > -0.30, "同领域不同命题曾被判成矛盾(1063 张伪冲突的来源)"

    contra = ("这个方案是可行的，风险可控", "这个方案不可行，风险不可控")
    assert emb.score(*contra) < -0.30, "同一命题的正反两面仍须判成矛盾"


if __name__ == "__main__":
    import tempfile
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            with tempfile.TemporaryDirectory() as d:
                try:
                    fn(Path(d))
                except TypeError:
                    fn()
            print(f"✓ {name}")
    print("\n误判冲突关闭 + 否定判定 回归通过")
