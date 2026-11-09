"""
HKC 持久化 manifest 校验测试(阶段 A 提交)。

覆盖范围:
  M1  save→load 8 步校验全过(roundtrip)
  M2  旧 v1 manifest(无 format_version 字段)→ _reset + load 返 False
  M3  format_version 升 3 → 校验失败 + _reset
  M4  ku_text_version 不一致 → 校验失败 + _reset(_ku_text 拼接变了,旧 hash 无意义)
  M5  backend 字段缺失 → 校验失败
  M6  dim 字段缺失/为 0/非 int → 校验失败
  M7  4 个 list 长度不一致 → 校验失败
  M8  faiss 实际维度 ≠ meta["dim"] → 校验失败
  M9  faiss 实际 ntotal ≠ len(ku_ids) → 校验失败
  M10 meta 文件损坏(非 pickle) → _reset + load 返 False
  M11 save invariant:4 list 长度不一致时抛 AssertionError
  M12 text_hashes 与 _ku_text 内容一致(同 KU 不同时间 hash 相同)

  B1  bm25 空 corpus → size=0,search 返 []
  B2  bm25 全空 doc(5 条)→ 不崩,size 计数正确,search 返 []
  B3  bm25 有一个非空 doc → 正常命中
  B4  bm25 未 build 时 search 返 []

设计原则(同 P0):
  - 不 mock:真 faiss + StubBackend + 真 VectorIndex + 真 BM25Index。
  - vendor 隔离硬阻断:模块级解析后立即自检 __file__,不是 vendor 就 RuntimeError。
  - 不依赖网络 / 真实模型;全程 tmp_path。
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import faiss
import numpy as np
import pytest

from hkc_search.vector_search import (
    VectorIndex,
    _MANIFEST_FORMAT_VERSION,
    _KU_TEXT_FORMAT_VERSION,
    _hash_text,
    _ManifestValidationError,
)
from hkc_search.bm25 import BM25Index
from hkc_search.embedding_backends import StubBackend
from hkc_core.models.ku import ConceptKU

_VENDOR_ROOT = Path(__file__).resolve().parents[1] / "vendor" / "hkc"


def _assert_vendor_path(obj, name: str) -> None:
    mod = obj.__module__
    f = getattr(sys.modules[mod], "__file__", "") or ""
    assert str(Path(f).resolve()).startswith(str(_VENDOR_ROOT.resolve())), (
        f"vendor isolation failed: {name}.__module__={mod!r} __file__={f!r} "
        f"not under vendor/hkc. Test is running against wrong HKC copy."
    )


for _cls_name, _cls in [
    ("VectorIndex",            VectorIndex),
    ("BM25Index",              BM25Index),
    ("StubBackend",            StubBackend),
    ("ConceptKU",              ConceptKU),
]:
    _assert_vendor_path(_cls, _cls_name)


@pytest.fixture
def stub_vector(tmp_path) -> VectorIndex:
    """已 build 3 条 KU 的 vector,save 后留磁盘文件。"""
    idx = VectorIndex(backend=StubBackend())
    kus = [
        ConceptKU(ku_id="KU_A", name="alpha",   summary="一种希腊字母",      domain="letter"),
        ConceptKU(ku_id="KU_B", name="beta",    summary="另一种希腊字母",    domain="letter"),
        ConceptKU(ku_id="KU_C", name="gamma",   summary="第三种希腊字母",    domain="letter"),
    ]
    idx.build(kus)
    return idx


def _ku_text(ku) -> str:
    """与 VectorIndex._ku_text 等价的复制(测试侧重新计算 hash 用)。"""
    parts = [ku.name]
    if ku.summary:
        parts.append(ku.summary)
    if ku.tags:
        parts.append(" ".join(ku.tags))
    stmt = getattr(ku, "statement", "")
    if stmt:
        parts.append(stmt)
    return " ".join(parts)


class TestManifestRoundtrip:
    def test_save_load_roundtrip(self, tmp_path, stub_vector):
        """save → load 完整,数据一致(ku_ids/names/summaries/text_hashes 全部保留)。"""
        vec_path = str(tmp_path / "search_vector")
        stub_vector.save(vec_path)
        meta = pickle.loads((Path(vec_path + ".meta")).read_bytes())
        assert meta["format_version"] == _MANIFEST_FORMAT_VERSION == 2
        assert meta["ku_text_version"] == _KU_TEXT_FORMAT_VERSION == 1
        assert meta["backend"].startswith("Stub("), meta["backend"]
        assert meta["dim"] == stub_vector.dim
        assert len(meta["ku_ids"]) == 3
        assert len(meta["text_hashes"]) == 3
        fresh = VectorIndex(backend=StubBackend())
        assert fresh.load(vec_path) is True
        assert fresh.size() == 3
        assert fresh._ku_ids == ["KU_A", "KU_B", "KU_C"]
        assert fresh._text_hashes == stub_vector._text_hashes

    def test_text_hash_matches_independent_compute(self, tmp_path, stub_vector):
        """text_hashes[i] 必须等于独立计算 _hash_text(_ku_text(ku_i)) —— 这才是
        阶段 B update_ku 能正确做差异检测的前提。"""
        expected = []
        for ku_id in stub_vector._ku_ids:
            i = stub_vector._ku_ids.index(ku_id)
            ku = ConceptKU(
                ku_id=ku_id,
                name=stub_vector._names[i],
                summary=stub_vector._summaries[i],
                domain="letter",
            )
            expected.append(_hash_text(_ku_text(ku)))
        assert stub_vector._text_hashes == expected

    def test_hash_stable_for_same_text(self):
        """同文本 → 同 hash(阶段 B 跨进程比对、阶段 C 续跑检测都要靠这个稳定性)。"""
        a = _hash_text("alpha 一种希腊字母")
        b = _hash_text("alpha 一种希腊字母")
        c = _hash_text("alpha 一种希腊字母 ")
        assert a == b
        assert a != c


class TestManifestValidation:
    def _fresh_v2_disk(self, tmp_path) -> str:
        """构造一份合法的 v2 磁盘,返回 prefix path。"""
        vec_path = str(tmp_path / "search_vector")
        v = VectorIndex(backend=StubBackend())
        kus = [ConceptKU(ku_id="KU_A", name="a", summary="a", domain="d"),
               ConceptKU(ku_id="KU_B", name="b", summary="b", domain="d")]
        v.build(kus)
        v.save(vec_path)
        return vec_path

    def test_v1_manifest_no_format_version_resets(self, tmp_path):
        """M2:旧 v1 manifest(无 format_version 字段)→ _reset。"""
        vec_path = self._fresh_v2_disk(tmp_path)
        with open(vec_path + ".meta", "wb") as f:
            pickle.dump({"ku_ids": ["X"], "names": ["x"],
                         "summaries": ["x"], "backend": "stub"}, f)
        v = VectorIndex(backend=StubBackend())
        assert v.load(vec_path) is False
        assert v.size() == 0
        assert v._ku_ids == []
        assert v._text_hashes == []

    def test_format_version_bump_resets(self, tmp_path):
        """M3:format_version 升 3 → 校验失败 + _reset。"""
        vec_path = self._fresh_v2_disk(tmp_path)
        with open(vec_path + ".meta", "rb") as f:
            meta = pickle.load(f)
        meta["format_version"] = 3
        with open(vec_path + ".meta", "wb") as f:
            pickle.dump(meta, f)
        v = VectorIndex(backend=StubBackend())
        assert v.load(vec_path) is False
        assert v.size() == 0

    def test_ku_text_version_drift_resets(self, tmp_path):
        """M4:ku_text_version 改了 → 校验失败(旧 hash 在新拼接下不可信)。"""
        vec_path = self._fresh_v2_disk(tmp_path)
        with open(vec_path + ".meta", "rb") as f:
            meta = pickle.load(f)
        meta["ku_text_version"] = 99
        with open(vec_path + ".meta", "wb") as f:
            pickle.dump(meta, f)
        v = VectorIndex(backend=StubBackend())
        assert v.load(vec_path) is False
        assert v.size() == 0

    def test_backend_field_missing_resets(self, tmp_path):
        """M5:backend 字段缺失 → 校验失败。"""
        vec_path = self._fresh_v2_disk(tmp_path)
        with open(vec_path + ".meta", "rb") as f:
            meta = pickle.load(f)
        del meta["backend"]
        with open(vec_path + ".meta", "wb") as f:
            pickle.dump(meta, f)
        v = VectorIndex(backend=StubBackend())
        assert v.load(vec_path) is False

    def test_dim_field_invalid_resets(self, tmp_path):
        """M6:dim 字段缺失/为 0/非 int → 校验失败。"""
        vec_path = self._fresh_v2_disk(tmp_path)
        for bad in (None, 0, "not_an_int", -1):
            with open(vec_path + ".meta", "rb") as f:
                meta = pickle.load(f)
            meta["dim"] = bad
            with open(vec_path + ".meta", "wb") as f:
                pickle.dump(meta, f)
            v = VectorIndex(backend=StubBackend())
            assert v.load(vec_path) is False, f"dim={bad!r} 应校验失败"
            assert v.size() == 0

    def test_list_length_mismatch_resets(self, tmp_path):
        """M7:4 list 长度不一致 → 校验失败。"""
        vec_path = self._fresh_v2_disk(tmp_path)
        with open(vec_path + ".meta", "rb") as f:
            meta = pickle.load(f)
        meta["text_hashes"] = meta["text_hashes"][:1]
        with open(vec_path + ".meta", "wb") as f:
            pickle.dump(meta, f)
        v = VectorIndex(backend=StubBackend())
        assert v.load(vec_path) is False

    def test_faiss_dim_mismatch_resets(self, tmp_path):
        """M8:faiss 实际维度与 meta["dim"] 不一致(切后端后常见)→ 校验失败。"""
        vec_path = self._fresh_v2_disk(tmp_path)
        wrong_idx = faiss.IndexFlatIP(999)
        wrong_idx.add(np.random.rand(2, 999).astype("float32"))
        faiss.write_index(wrong_idx, vec_path + ".faiss")
        v = VectorIndex(backend=StubBackend())
        assert v.load(vec_path) is False
        assert v.size() == 0

    def test_faiss_ntotal_mismatch_resets(self, tmp_path):
        """M9:faiss 实际 ntotal 与 meta len(ku_ids) 不一致 → 校验失败。"""
        vec_path = self._fresh_v2_disk(tmp_path)
        wrong_idx = faiss.IndexFlatIP(stub_vector_dim := VectorIndex(backend=StubBackend()).dim)
        wrong_idx.add(np.random.rand(99, stub_vector_dim).astype("float32"))
        faiss.write_index(wrong_idx, vec_path + ".faiss")
        v = VectorIndex(backend=StubBackend())
        assert v.load(vec_path) is False
        assert v.size() == 0

    def test_corrupt_meta_file_resets(self, tmp_path):
        """M10:meta 文件非 pickle → _reset + load 返 False。"""
        vec_path = self._fresh_v2_disk(tmp_path)
        with open(vec_path + ".meta", "wb") as f:
            f.write(b"\x00\x01not-a-pickle\xff")
        v = VectorIndex(backend=StubBackend())
        assert v.load(vec_path) is False
        assert v.size() == 0

    def test_save_invariant_assertion(self, tmp_path):
        """M11:save() 时 4 list 长度不一致 → 抛 AssertionError(不让坏 manifest 落盘)。"""
        v = VectorIndex(backend=StubBackend())
        kus = [ConceptKU(ku_id="KU_A", name="a", summary="a", domain="d")]
        v.build(kus)
        v._text_hashes = v._text_hashes[:0]
        with pytest.raises(AssertionError):
            v.save(str(tmp_path / "search_vector"))

    def test_validation_error_has_reason_code(self, tmp_path):
        """_ManifestValidationError.reason 是机器可读码,供上层 logger/log metric 用。"""
        try:
            raise _ManifestValidationError("foo_reason", "foo msg")
        except _ManifestValidationError as e:
            assert e.reason == "foo_reason"
            assert "foo msg" in str(e)


class TestBM25EmptyCorpus:
    def test_build_empty_list(self):
        """B1:build([]) → 索引为空,search 返 []。"""
        b = BM25Index()
        b.build([])
        assert b.size() == 0
        assert b.search("anything") == []

    def test_build_all_empty_docs(self):
        """B2:全空 doc(name/summary/tags 皆空)→ 不崩,size 计数正确,search 返 []。
        触发场景:append_ku 首个 KU 时拼出的 token 列表为空。"""
        b = BM25Index()
        for i in range(5):
            b.append_ku(ConceptKU(ku_id=f"KU_{i}", name="", summary=""))
        assert b.size() == 5
        assert b.search("x") == []

    def test_build_one_valid_doc(self):
        """B3:哪怕只有一个非空 doc 也能正常命中。"""
        b = BM25Index()
        b.append_ku(ConceptKU(ku_id="KU_X", name="x", summary="x is a letter"))
        r = b.search("x letter", top_k=1)
        assert len(r) == 1 and r[0].ku_id == "KU_X"

    def test_search_with_no_bm25(self):
        """B4:未 build 时 search 返 []。"""
        b = BM25Index()
        assert b.search("anything") == []
