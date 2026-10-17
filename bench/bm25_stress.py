"""
bench/bm25_stress.py
5万/10万 corpus 压测 + remove_ku 增量测试。
"""
import os, sys, random, time
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vendor'))
from hkc.hkc_search.bm25 import BM25Index as NewBM25, _tokenize


@dataclass
class MockKU:
    ku_id:   str
    name:    str
    summary: str
    tags:    list[str]


TOPICS_EN = ["machine learning","neural network","deep learning","data science",
    "natural language","computer vision","reinforcement learning",
    "transformer model","graph neural network","knowledge graph",
    "vector database","embedding model","semantic search","retrieval augmented",
    "transformer architecture","attention mechanism","language model",
    "encoder decoder","self supervised","contrastive learning"] * 5
ACTIONS = ["tutorial","review","introduction","advanced","case study","comparison"] * 10
LANGS   = ["python","rust","cpp","java","go","typescript"] * 10
TOPICS_CN = ["机器学习","神经网络","深度学习","数据科学","自然语言处理",
    "计算机视觉","强化学习","图神经网络","知识图谱","向量数据库",
    "嵌入模型","语义搜索","检索增强","注意力机制","语言模型","自监督学习"] * 5


def gen_corpus(n, seed=42):
    rng = random.Random(seed)
    corpus = []
    for i in range(n):
        k = rng.randint(1, 3)
        topics = [rng.choice(TOPICS_EN) for _ in range(k)]
        if rng.random() < 0.5:
            topics.append(rng.choice(TOPICS_CN))
        action = rng.choice(ACTIONS)
        lang = rng.choice(LANGS)
        name = f"{' '.join(topics)} {action} ({lang}) #{i}"
        summary = f"This is a {action} about {' '.join(topics)} in {lang}. " \
                  f"Topics: {', '.join(topics)}."
        tags = topics + [action, lang]
        corpus.append(MockKU(ku_id=f"ku_{i:08d}", name=name, summary=summary, tags=tags))
    return corpus


def gen_queries(n, seed=99):
    rng = random.Random(seed)
    queries = []
    for _ in range(n):
        k = rng.randint(1, 3)
        terms = [rng.choice(TOPICS_EN) for _ in range(k)]
        if rng.random() < 0.3:
            terms.append(rng.choice(TOPICS_CN))
        if rng.random() < 0.4:
            terms.append(rng.choice(ACTIONS))
        queries.append(" ".join(terms))
    return queries


def bench_build(n):
    print(f"\n[build] {n} corpus ...")
    corpus = gen_corpus(n)
    t0 = time.time()
    idx = NewBM25()
    idx.build(corpus)
    dt = time.time() - t0
    print(f"        build: {dt*1000:.0f} ms ({dt*1000/n:.3f} ms/doc)")
    return idx, corpus


def bench_search(idx, queries):
    t0 = time.time()
    total_hits = 0
    for q in queries:
        r = idx.search(q, top_k=10)
        total_hits += len(r)
    dt = time.time() - t0
    print(f"        search {len(queries)} query: {dt*1000:.0f} ms ({dt*1000/len(queries):.2f} ms/query, {total_hits} hits)")
    return dt


def bench_remove(idx, n_remove=1000):
    print(f"\n[remove] 删 {n_remove} 条 (random) ...")
    import random
    rng = random.Random(123)
    to_remove = rng.sample(idx._doc_ids, n_remove)  # noqa
    t0 = time.time()
    for ku_id in to_remove:
        idx.remove_ku(ku_id)
    dt = time.time() - t0
    print(f"        remove: {dt*1000:.0f} ms ({dt*1000/n_remove:.3f} ms/op)")
    print(f"        size after: {idx.size()}")


def bench_save_load(idx, path):
    print(f"\n[save/load] {path} ...")
    t0 = time.time()
    idx.save(path)
    t_save = time.time() - t0
    sz = os.path.getsize(path)
    t0 = time.time()
    idx2 = NewBM25()
    ok = idx2.load(path)
    t_load = time.time() - t0
    print(f"        save : {t_save*1000:.0f} ms ({sz/1024/1024:.1f} MB)")
    print(f"        load : {t_load*1000:.0f} ms (ok={ok}, size={idx2.size()})")


def main():
    print("=" * 60)
    print("BM25 压测: 5万/10万 corpus build + search + remove + save/load")
    print("=" * 60)

    # 5万
    idx, corpus = bench_build(50_000)
    queries = gen_queries(200)
    bench_search(idx, queries)
    bench_remove(idx, 1000)
    tmp = "_tmp_bm25_50k.pkl"
    bench_save_load(idx, tmp)
    os.remove(tmp)

    # 10万 (增量:在 5万 上 append 5万,模拟冷构建期分批)
    print(f"\n[build] +5万 增量 (5万 -> 10万) ...")
    extra_corpus = gen_corpus(50_000, seed=43)
    # 取后 5万 (id 范围 0-49999 是 5万 corpus;这里 gen 同一 seed 重叠,改 seed)
    # 实际:重新 gen 5万不同 id
    extra_corpus = []
    rng = random.Random(43)
    for i in range(50_000, 100_000):
        k = rng.randint(1, 3)
        topics = [rng.choice(TOPICS_EN) for _ in range(k)]
        if rng.random() < 0.5:
            topics.append(rng.choice(TOPICS_CN))
        action = rng.choice(ACTIONS)
        lang = rng.choice(LANGS)
        name = f"{' '.join(topics)} {action} ({lang}) #{i}"
        summary = f"This is a {action} about {' '.join(topics)} in {lang}. Topics: {', '.join(topics)}."
        tags = topics + [action, lang]
        extra_corpus.append(MockKU(ku_id=f"ku_{i:08d}", name=name, summary=summary, tags=tags))
    t0 = time.time()
    idx.append_batch(extra_corpus)
    dt = time.time() - t0
    print(f"        append 5万 增量: {dt*1000:.0f} ms ({dt*1000/50_000:.3f} ms/doc)")
    print(f"        size after: {idx.size()}")
    bench_search(idx, queries)

    print("\n" + "=" * 60)
    print("压测完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
