"""vector 索引升级 + 性能对比:Flat vs IVF(1500 mock KU, stub backend)

目的:验证 HKC_VECTOR_IVF_THRESHOLD 触发的 Flat → IVF 自动升级,
     并对比两种索引在 500 / 1500 mock KU 下的检索延迟。

用法:
    python bench/vector_ivf_upgrade.py

可调参数:
    HKC_VECTOR_IVF_THRESHOLD  默认 500(本脚本覆盖)
    IVF_NLIST                 自定义 nlist(可选)
    TOTAL_KUS                 总 mock KU 数,默认 1500
"""
from __future__ import annotations

import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

os.environ["HKC_VECTOR_IVF_THRESHOLD"] = "500"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vendor.hkc.hkc_search.vector_search import VectorIndex
from vendor.hkc.hkc_search.embedding_backends import StubBackend


def make_ku(i: int) -> SimpleNamespace:
    """构造 mock KU,字段匹配 vector_search._ku_text() 期望的 4 项: name/summary/tags/statement。"""
    return SimpleNamespace(
        ku_id     = f"ku_{i:06d}",
        name      = f"entity_{i}",
        summary   = f"summary of entity {i} about topic {i % 50}",
        tags      = [f"tag_{i % 10}", f"category_{i % 5}"],
        statement = f"fact {i}: something interesting about subject {i}",
    )


def bench_search(idx, query: str, n: int = 100, top_k: int = 10) -> tuple[float, float, float]:
    """跑 n 次 search,返 (median_ms, min_ms, max_ms)。"""
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        idx.search(query, top_k=top_k)
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times), min(times), max(times)


def main() -> None:
    backend = StubBackend(dim=384)
    threshold = int(os.environ["HKC_VECTOR_IVF_THRESHOLD"])

    print("=" * 70)
    print(f"backend       : {backend.name}")
    print(f"threshold     : HKC_VECTOR_IVF_THRESHOLD = {threshold}")
    print(f"upgrade to    : IndexIVFFlat(nlist≈√N, nprobe=16)")
    print("=" * 70)

    N = 5000
    print(f"\n构造 {N} 条 mock KU...")
    kus = [make_ku(i) for i in range(N)]

    idx = VectorIndex(backend=backend)
    print(f"index type 初始: {type(idx._index).__name__}")

    phase1_n = threshold - 1
    print(f"\n[Phase 1] append {phase1_n} 条(< {threshold},应保持 Flat)...")
    for ku in kus[:phase1_n]:
        idx.append_ku(ku)
    print(f"  index type: {type(idx._index).__name__}, ntotal={idx._index.ntotal}")
    med, mn, mx = bench_search(idx, "find something about topic 7")
    print(f"  search ×100: median={med:.2f}ms  min={mn:.2f}ms  max={mx:.2f}ms")

    print(f"\n[Phase 2] append 第 {threshold} 条(应触发升级)...")
    t0 = time.perf_counter()
    idx.append_ku(kus[phase1_n])
    upgrade_ms = (time.perf_counter() - t0) * 1000
    print(f"  append(含升级)耗时: {upgrade_ms:.1f}ms")
    print(f"  index type: {type(idx._index).__name__}, ntotal={idx._index.ntotal}")
    if hasattr(idx._index, "nlist"):
        print(f"  IVF nlist={idx._index.nlist}, nprobe={idx._index.nprobe}")
    med, mn, mx = bench_search(idx, "find something about topic 7")
    print(f"  search ×100: median={med:.2f}ms  min={mn:.2f}ms  max={mx:.2f}ms")

    rest = N - threshold
    print(f"\n[Phase 3] append {rest} 条(应直接 add 到 IVF)...")
    t0 = time.perf_counter()
    for ku in kus[threshold:]:
        idx.append_ku(ku)
    bulk_ms = (time.perf_counter() - t0) * 1000
    print(f"  {rest} 条 append 耗时: {bulk_ms:.1f}ms(平均 {bulk_ms/rest:.2f}ms/条)")
    print(f"  index type: {type(idx._index).__name__}, ntotal={idx._index.ntotal}")
    med, mn, mx = bench_search(idx, "find something about topic 7")
    print(f"  search ×100: median={med:.2f}ms  min={mn:.2f}ms  max={mx:.2f}ms")

    print(f"\n[Phase 4] save/load roundtrip(IVF 持久化)...")
    with tempfile.TemporaryDirectory() as td:
        p = str(Path(td) / "vec")
        t0 = time.perf_counter()
        idx.save(p)
        save_ms = (time.perf_counter() - t0) * 1000
        idx2 = VectorIndex(backend=backend)
        t0 = time.perf_counter()
        ok = idx2.load(p)
        load_ms = (time.perf_counter() - t0) * 1000
        n_loaded = idx2._index.ntotal if idx2._index else 0
        type_loaded = type(idx2._index).__name__ if idx2._index else "None"
        print(f"  save: {save_ms:.1f}ms  load: {load_ms:.1f}ms  ok={ok}")
        print(f"  loaded ntotal={n_loaded}, type={type_loaded}")
        if n_loaded != N:
            print(f"  [FAIL] 数据丢失! 期望 {N} 实际 {n_loaded}")
        elif "FlatIP" in type_loaded:
            print(f"  [FAIL] IVF 没保存,降回 FlatIP")
        else:
            print(f"  [OK] IVF 完整保存/加载(ntotal={n_loaded}, type={type_loaded})")

    print("\n" + "=" * 70)
    print("[OK] benchmark 完成")
    print("=" * 70)


if __name__ == "__main__":
    main()
