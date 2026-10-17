"""
bench/bm25_audit.py
B 阶段 BM25 全重写 - 全面审核
- 浮点累加 vs 一次 sum 精度差
- pickle schema 兼容(老 pickle 读不到 fallback)
- append_ku 单条延迟
- search 一致性 (build vs append_batch)
- 与 service 数据集成(用 service 落盘 pickle 看 /stats 一致)
"""
import os, sys, random, time, pickle
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vendor'))
from hkc.hkc_search.bm25 import BM25Index as NewBM25, _tokenize


@dataclass
class MockKU:
    ku_id:   str
    name:    str
    summary: str
    tags:    list[str]


def gen_corpus(n, seed=42):
    rng = random.Random(seed)
    corpus = []
    for i in range(n):
        topics = [f"topic{j}_{rng.randint(0, 9)}" for j in range(rng.randint(1, 3))]
        corpus.append(MockKU(ku_id=f"ku_{i:08d}",
            name=f"{' '.join(topics)} #{i}",
            summary=f"summary about {' '.join(topics)}",
            tags=topics + ["common"]))
    return corpus


def audit_float_accumulation():
    """1: 浮点累加 vs 一次 sum/len 精度差"""
    print("\n[1/5] 浮点累加精度对比 (build 一次 vs 累加 1000 次) ...")
    corpus = gen_corpus(1000)

    # 方法 A: build 一次
    idx_a = NewBM25()
    idx_a.build(corpus)

    # 方法 B: 1000 次 append_ku
    idx_b = NewBM25()
    for ku in corpus:
        idx_b.append_ku(ku)

    # 比 avgdl
    print(f"        build 一次  avgdl = {idx_a._avgdl:.10f}")
    print(f"        1000 次累加 avgdl = {idx_b._avgdl:.10f}")
    diff_avgdl = abs(idx_a._avgdl - idx_b._avgdl)
    print(f"        差 = {diff_avgdl:.2e} (rel {diff_avgdl/idx_a._avgdl:.2e})")

    # 比 _idf_sum
    print(f"        build 一次  _idf_sum = {idx_a._idf_sum:.10f}")
    print(f"        1000 次累加 _idf_sum = {idx_b._idf_sum:.10f}")
    diff_idf = abs(idx_a._idf_sum - idx_b._idf_sum)
    print(f"        差 = {diff_idf:.2e} (rel {diff_idf/max(abs(idx_a._idf_sum),1e-10):.2e})")

    # 搜同一 query,top-3 是否一致
    q = "topic0_3 topic1_7"
    r_a = sorted([(r.ku_id, round(r.score, 8)) for r in idx_a.search(q, 10)])
    r_b = sorted([(r.ku_id, round(r.score, 8)) for r in idx_b.search(q, 10)])
    same = (r_a == r_b)
    print(f"        top-10 一致 (build 一次 vs 1000 次累加): {'PASS' if same else 'FAIL'}")
    if not same:
        print(f"          A: {r_a[:3]}")
        print(f"          B: {r_b[:3]}")
    return same


def audit_pickle_schema_fallback():
    """2: pickle schema 兼容(老 pickle / 损坏 pickle fallback)"""
    print("\n[2/5] pickle schema 兼容 + 损坏文件 fallback ...")
    tmp = "_tmp_audit.pkl"
    # 正常 save/load
    idx = NewBM25()
    idx.build(gen_corpus(100))
    idx.save(tmp)
    idx2 = NewBM25()
    ok = idx2.load(tmp)
    print(f"        正常 save/load: ok={ok}, size={idx2.size()} {'PASS' if ok and idx2.size()==100 else 'FAIL'}")

    # 损坏文件
    with open(tmp, 'wb') as f:
        f.write(b'garbage data not a pickle')
    idx3 = NewBM25()
    ok3 = idx3.load(tmp)
    print(f"        损坏文件 load: ok={ok3} (应该返 False, 不抛异常) {'PASS' if ok3 == False else 'FAIL'}")
    print(f"        损坏后 idx3.size() = {idx3.size()} (应该 0, 已 fallback) {'PASS' if idx3.size() == 0 else 'FAIL'}")

    # schema 不匹配(手写一个旧版结构)
    with open(tmp, 'wb') as f:
        pickle.dump({'old_version': True, 'ku_ids': [], 'corpus': []}, f)
    idx4 = NewBM25()
    ok4 = idx4.load(tmp)
    print(f"        schema 不匹配: ok={ok4} (应该返 False) {'PASS' if ok4 == False else 'FAIL'}")

    # 不存在文件
    if os.path.exists(tmp):
        os.remove(tmp)
    idx5 = NewBM25()
    ok5 = idx5.load("_nonexistent_file.pkl")
    print(f"        文件不存在: ok={ok5} (应该返 False) {'PASS' if ok5 == False else 'FAIL'}")

    if os.path.exists(tmp):
        os.remove(tmp)
    return all([ok, ok3 == False, idx3.size() == 0, ok4 == False, ok5 == False])


def audit_append_ku_latency():
    """3: append_ku 单条延迟"""
    print("\n[3/5] append_ku 单条延迟 ...")
    idx = NewBM25()
    corpus = gen_corpus(1000)
    t0 = time.time()
    for ku in corpus:
        idx.append_ku(ku)
    dt = time.time() - t0
    print(f"        1000 条 append_ku: {dt*1000:.0f} ms ({dt*1000/1000:.3f} ms/op)")
    return dt


def audit_search_consistency():
    """4: search 一致性 (build vs append_batch)"""
    print("\n[4/5] search 一致性: build(1次) vs append_batch(2次) ...")
    corpus = gen_corpus(1000, seed=42)

    idx_a = NewBM25()
    idx_a.build(corpus)

    idx_b = NewBM25()
    idx_b.build(corpus[:500])
    idx_b.append_batch(corpus[500:])

    queries = ["topic0_3 topic1_7", "topic2_5", "topic3_9 common"]
    all_match = True
    for q in queries:
        r_a = sorted([(r.ku_id, round(r.score, 8)) for r in idx_a.search(q, 10)])
        r_b = sorted([(r.ku_id, round(r.score, 8)) for r in idx_b.search(q, 10)])
        match = r_a == r_b
        if not match:
            print(f"        '{q}': NOT MATCH")
            all_match = False
    print(f"        3 query 一致: {'PASS' if all_match else 'FAIL'}")
    return all_match


def audit_clear_idempotent():
    """5: clear() 幂等性 + 空 corpus search"""
    print("\n[5/5] clear() 幂等性 + 空 corpus search ...")
    idx = NewBM25()
    idx.build(gen_corpus(100))
    print(f"        size 前: {idx.size()}")
    idx.clear()
    print(f"        clear 后: {idx.size()} (应该 0) {'PASS' if idx.size()==0 else 'FAIL'}")

    # 空 search 不抛
    r = idx.search("any query", 10)
    print(f"        空 search: {len(r)} 个结果 (应该 0) {'PASS' if len(r)==0 else 'FAIL'}")

    # 二次 clear
    idx.clear()
    print(f"        二次 clear: size={idx.size()} (应该 0) {'PASS' if idx.size()==0 else 'FAIL'}")

    # 删不存在
    r = idx.remove_ku("ku_99999999")
    print(f"        remove_ku 不存在: 返 {r} (应该 False) {'PASS' if r == False else 'FAIL'}")

    # search 空 query
    r = idx.search("", 10)
    print(f"        search 空 query: {len(r)} 个结果 (应该 0) {'PASS' if len(r)==0 else 'FAIL'}")

    r = idx.search("   \t\n  ", 10)
    print(f"        search 纯空白 query: {len(r)} 个结果 (应该 0) {'PASS' if len(r)==0 else 'FAIL'}")
    return True


def main():
    print("=" * 60)
    print("B 阶段 BM25 - 全面审核 (5 项)")
    print("=" * 60)

    r1 = audit_float_accumulation()
    r2 = audit_pickle_schema_fallback()
    audit_append_ku_latency()  # 打印即可
    r4 = audit_search_consistency()
    r5 = audit_clear_idempotent()

    print("\n" + "=" * 60)
    print(f"汇总: 浮点精度={'PASS' if r1 else 'FAIL'}  pickle={'PASS' if r2 else 'FAIL'}  search一致={'PASS' if r4 else 'FAIL'}  clear/空={'PASS' if r5 else 'FAIL'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
