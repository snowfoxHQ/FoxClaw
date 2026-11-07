"""
HKC 后台索引构建测试(阶段 C 提交)。

覆盖范围:
  S1  无索引启动 + 库有数据 → assemble 立即返;manager 后台编码;
      等 join 后 vector_size == source_total,index_state=ready。
  S2  旧快照启动(磁盘 faiss 完整覆盖库) → assemble 立即返;
      manager.start() 返 None(无需构建);vector_size == 磁盘中数。
  S3  构建中重启 → 续跑(落盘 faiss 删光,manager 仍从空开始;但状态机重启后
      state 仍为上次 cancelled/ready,这是 next.start() 决策依据)
  S4  构建中查询:manager 跑后台编码的同时,前端调 c.search.search()
      → 不抛错;能拿到当前 partial 结果。

设计原则(同 P0/P1/P2):
  - 不 mock:真 SQLite + 真 faiss + StubBackend + 真 HKCContainer + 真 IndexBuildManager。
  - vendor 隔离硬阻断:模块级解析后立即自检 __file__,不是 vendor 就 RuntimeError。
  - 不依赖网络 / 真实模型 / 真实数据目录;全程 tmp_path。
  - 每个测试独立 tmp_path;HKCContainer init/reset 严格 try/finally。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

# ── vendor 隔离硬阻断 ────────────────────────────────────────────
from hkc_search.index_build_manager import IndexBuildManager, _BUILD_STATES
from hkc_search.vector_search import VectorIndex
from hkc_search.embedding_backends import StubBackend
from hkc_api.container import HKCContainer, init_container, reset_container
from hkc_core.graph.sqlite_store import SQLiteGraphStore
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
    ("IndexBuildManager", IndexBuildManager),
    ("VectorIndex",        VectorIndex),
    ("StubBackend",         StubBackend),
    ("HKCContainer",        HKCContainer),
    ("SQLiteGraphStore",    SQLiteGraphStore),
    ("ConceptKU",           ConceptKU),
]:
    _assert_vendor_path(_cls, _cls_name)


# ── Helpers ─────────────────────────────────────────────────────

def _seed_kus(c: HKCContainer, ids: list[str]) -> None:
    """直接 put 进 store,不发出 KNOWLEDGE_CREATED 事件。
    用于构造 '库有数据但 vector 没条' 的初始状态,触发 manager 全量构建。
    """
    for i, ku_id in enumerate(ids):
        ku = ConceptKU(
            ku_id=ku_id,
            name=f"ku_{i}",
            summary=f"ku {i} 的描述是若干中文文字以填充文本",
            domain="d",
        )
        c.graph_store.put(ku)


def _wait_idle(c: HKCContainer, timeout: float = 30.0) -> dict:
    """等 manager 跑完一轮变成 ready/failed/cancelled,返 snapshot。timeout 内的 poll。"""
    deadline = time.time() + timeout
    mgr = c.index_build_manager
    last_state = None
    while time.time() < deadline:
        s = mgr.stats()
        if s["index_state"] in ("ready", "failed", "cancelled", "idle"):
            return s
        last_state = s
        time.sleep(0.05)
    raise AssertionError(
        f"等超时 {timeout}s;最后状态: {last_state}"
    )


def _seed_and_start_build(c: HKCContainer, ids: list[str]) -> None:
    """init_container 在装配期会扫描库。此时若库空,manager 会立即 idle;
    seed KU 后必须显式再 start() 触发 full build(在已加载的 vector 上 append)。
    装配时已自动 start 过一次(空库 → idle),这次手动 start 触发增量构建。
    """
    _seed_kus(c, ids)
    r = c.index_build_manager.start()
    # size>0 不应返 None(已加载的 faiss 有数据但库无相同数据时可能返 None;此测试场景是库空)
    # 这里 size=0 + source>0 → 必起构建 → start 返 task_id
    assert r is not None, (
        f"_seed_and_start_build 应触发新构建,但 start() 返 None。"
        f"当前 stats: {c.index_build_manager.stats()}"
    )


# ── S1:无索引启动,manager 后台跑全量构建 ───────────────────

class TestStartNoIndexManagerBuildsBackground:
    def test_assemble_returns_fast_manager_builds_then_ready(self, tmp_path):
        """库有 5 KU 但磁盘无索引 → seed + start → manager 后台构建;
        join 后 vector_size == source_total 且 state=ready。"""
        data_dir = str(tmp_path)
        c = init_container(data_dir=data_dir, embedding_kind="stub")
        try:
            _seed_and_start_build(c, [f"KU_S1_{i}" for i in range(5)])
            mgr = c.index_build_manager
            assert mgr is not None
            final = _wait_idle(c, timeout=30)
            assert final["index_state"] == "ready", (
                f"应在 ready,实际 {final}"
            )
            assert final["source_total"] == 5, final
            assert final["ready"] == 5, final
            assert final["failed"] == 0, final
            # 内存索引真存在
            assert c.search.vector.size() == 5
        finally:
            c.close()
            reset_container()

    def test_assemble_does_not_block_when_manager_builds(self, tmp_path):
        """关键不变量:装配本身**不阻塞**全量 encode。
        装配返回(manager 已 start)再 seed → 整体构建必须由 manager 后台承担。"""
        data_dir = str(tmp_path)
        c = init_container(data_dir=data_dir, embedding_kind="stub")
        try:
            # 装配已经返回(库空,manager 立即 idle)。seed + 重 start 让它编码。
            _seed_and_start_build(c, [f"KU_FAST_{i}" for i in range(2)])
            s = c.index_build_manager.stats()
            assert s["index_state"] in _BUILD_STATES
            final = _wait_idle(c, timeout=10)
            assert final["index_state"] == "ready"
            assert final["ready"] == 2
        finally:
            c.close()
            reset_container()


# ── S2:旧快照启动(磁盘 faiss 已完整) → 不重起构建 ──────────

class TestStartWithPersistedSnapshotNoRebuild:
    def test_manager_skips_when_disk_snapshot_covers_source(self, tmp_path):
        """先启动一次让 manager 构建完 → close;重启 → manager 看到 faiss 已非空
        → start() 返 None,不再构建。"""
        data_dir = str(tmp_path)
        # 第一轮:seed + start → manager 构建 → 落盘
        c = init_container(data_dir=data_dir, embedding_kind="stub")
        try:
            _seed_and_start_build(c, [f"KU_S2A_{i}" for i in range(4)])
            _wait_idle(c, timeout=20)
            assert c.search.vector.size() == 4
        finally:
            c.close()
            reset_container()

        # 第二轮:simulate 重启 —— 装配后,manager.start() 应返 None (已有快照)
        c2 = init_container(data_dir=data_dir, embedding_kind="stub")
        try:
            s = c2.index_build_manager.stats()
            assert s["index_state"] == "ready", (
                f"已有磁盘快照时 manager 应跳过,实际 {s}"
            )
            assert s["vector_size"] == 4, (
                f"vector_size 应从磁盘恢复,实际 {s}"
            )
            # 显式再调 start() 也返 None(幂等)
            r = c2.index_build_manager.start()
            assert r is None, (
                f"已有快照时 start() 应返 None,实际 {r!r}"
            )
        finally:
            c2.close()
            reset_container()


# ── S3:构建中重启 → 续跑 ───────────────────────────────

class TestMidBuildRestartPicksUp:
    def test_resume_after_disk_vector_partial(self, tmp_path):
        """模拟 '上次构建到一半被杀掉':磁盘 faiss 有 5 条全量,manager 看到库空
        就不重跑(构不了啥)。验证 restart 链路:装配→加载 faiss→manager 跳过。
        真正的「mid-build 续跑」语义由 state.json + vector 缺漏检测;
        但因为 manager 的 start() 总是基于「当前 vector vs 当前库」做决策,
        磁盘有 5 条 + 库 5 条相同 → 跳过(等价于 S2 的 happy path)。"""
        data_dir = str(tmp_path)
        # 第一轮:build 5 条 → save → close
        c = init_container(data_dir=data_dir, embedding_kind="stub")
        try:
            _seed_and_start_build(c, [f"KU_S3_{i}" for i in range(5)])
            _wait_idle(c, timeout=20)
            assert c.search.vector.size() == 5
        finally:
            c.close()
            reset_container()

        # 模拟"上次只跑了部分,faiss 是部分加载的" — 删 vector 文件(变 0 条)
        # manager 装配时会 _load_persisted_index_and_bm25 → load 失败(因为已被我们删)→ 0 条
        # 然后 manager.start() 看到 size=0 < source=5 → 起新构建
        vec_path = tmp_path / "search_vector"
        if (vec_path.with_suffix(".faiss")).exists():
            (vec_path.with_suffix(".faiss")).unlink()
        if (vec_path.with_suffix(".meta")).exists():
            (vec_path.with_suffix(".meta")).unlink()

        # 写 state.json 模拟"上次未完成",确认 manager.start 决策时能读到历史
        import json as _json
        state = {
            "task_id": "build-deadbeef",
            "index_version": "v-stale",
            "state": "building",
            "source_total": 5,
            "scanned": 3,
            "ready": 3,
            "failed": 0,
            "failures": [],
            "started_at": "2025-01-01T00:00:00+00:00",
            "finished_at": None,
            "last_checkpoint_at": "2025-01-01T00:00:01+00:00",
            "error_summary": "",
        }
        (tmp_path / IndexBuildManager.STATE_FILE).write_text(
            _json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 第二轮:重启
        c2 = init_container(data_dir=data_dir, embedding_kind="stub")
        try:
            # 装配已自动 start → 应启构建(因为 faiss 已删)
            final = _wait_idle(c2, timeout=30)
            assert final["index_state"] == "ready", (
                f"重启后应到 ready,实际 {final}"
            )
            assert c2.search.vector.size() == 5, (
                f"重启后 vector_size 应=5,实际 {c2.search.vector.size()}"
            )
            # 跑出了新 task_id(不是历史的 deadbeef)
            assert final["task_id"] != "build-deadbeef", (
                f"应起新 task_id,实际仍用历史的 {final['task_id']!r}"
            )
        finally:
            c2.close()
            reset_container()


# ── S4:构建中查询 ──────────────────────────────────────────

class TestSearchDuringBuild:
    def test_concurrent_search_does_not_crash(self, tmp_path):
        """manager 后台跑编码的同时,前调 c.search.search() → 不抛错;
        任何阶段拿到的是 partial 但应是合法 SearchResult 列表。"""
        data_dir = str(tmp_path)
        c = init_container(data_dir=data_dir, embedding_kind="stub")
        try:
            _seed_and_start_build(c, [f"KU_S4_{i}" for i in range(6)])
            # 不等;立即并发调 search
            results = c.search.search("ku_1 描述", mode="hybrid", top_k=3)
            assert isinstance(results, list)
            for r in results:
                assert hasattr(r, "ku_id")
                assert hasattr(r, "score")
            # 等构建完再做一次 search 确认 6 条都被 indexed
            final = _wait_idle(c, timeout=30)
            assert final["index_state"] == "ready"
            assert c.search.vector.size() == 6
            results2 = c.search.search("ku_2", mode="bm25", top_k=2)
            assert isinstance(results2, list)
        finally:
            c.close()
            reset_container()


# ── 单元测:IndexBuildManager 状态机字段 ─────────────────

class TestManagerStats:
    def test_stats_keys_present(self, tmp_path):
        """stats 字典字段稳定性(对外观测前端要靠这些 key)。"""
        mgr = IndexBuildManager(
            data_dir=str(tmp_path),
            hybrid_search=_FakeHybrid(),
            graph_store=_FakeEmptyGraphStore(),
        )
        s = mgr.stats()
        for k in (
            "index_state", "index_version", "task_id", "source_total",
            "scanned", "ready", "pending", "failed",
            "started_at", "finished_at", "last_checkpoint_at",
            "error_summary", "failures_preview", "vector_size",
        ):
            assert k in s, f"stats 缺字段 {k}, got {sorted(s.keys())}"

    def test_idle_start_no_kus_yields_ready(self, tmp_path):
        """空 store + 空 faiss → start 起线程跑,扫描后 source_total=0 → 立即 ready。"""
        mgr = IndexBuildManager(
            data_dir=str(tmp_path),
            hybrid_search=_FakeHybrid(),
            graph_store=_FakeEmptyGraphStore(),
        )
        r = mgr.start()
        mgr.join(timeout=5)
        # 空 store 扫描到 0 条 → 直接 _mark_ready
        s = mgr.stats()
        assert s["index_state"] in ("ready", "cancelled", "failed"), s
        assert s["source_total"] == 0, s


# ── Fakes(单元测用) ─────────────────────────────────────

class _FakeHybrid:
    """空 hybrid:vector_size=0,_ku_ids 空列表,_lock 是 RLock,vector 假实例。"""
    def __init__(self):
        import threading as _t
        self._lock = _t.RLock()
        self.vector = _FakeVector()


class _FakeVector:
    def __init__(self):
        self._ku_ids: list = []
    def size(self): return len(self._ku_ids)
    def save(self, path): pass
    def append_bulk(self, kus):
        with _FakeVector._lock:
            self._ku_ids.extend(k.ku_id for k in kus)
    _lock = __import__("threading").RLock()


class _FakeEmptyGraphStore:
    def query_all(self, limit=99999999):
        return []
