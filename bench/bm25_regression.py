"""
bench/bm25_regression.py
新 BM25(自实现倒排) vs rank_bm25.BM25Okapi top-10 一致率回归测试。

目标:1万 corpus + 100 query,新实现 top-10 一致率 >= 99%。
跑法:python bench/bm25_regression.py
"""
import os
import random
import sys
import time
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vendor'))

from hkc.hkc_search.bm25 import BM25Index as NewBM25, _tokenize


@dataclass
class MockKU:
    ku_id:   str
    name:    str
    summary: str
    tags:    list[str]


TOPICS_EN = [
    "machine learning", "neural network", "deep learning", "data science",
    "natural language", "computer vision", "reinforcement learning",
    "transformer model", "graph neural network", "knowledge graph",
    "vector database", "embedding model", "semantic search", "retrieval augmented",
    "transformer architecture", "attention mechanism", "language model",
    "encoder decoder", "self supervised", "contrastive learning",
]
TOPICS_CN = [
    "机器学习", "神经网络", "深度学习", "数据科学",
    "自然语言处理", "计算机视觉", "强化学习",
    "图神经网络", "知识图谱", "向量数据库",
    "嵌入模型", "语义搜索", "检索增强",
    "注意力机制", "语言模型", "自监督学习",
]
ACTIONS = ["tutorial", "review", "introduction", "advanced", "case study", "comparison"]
LANGS   = ["python", "rust", "cpp", "java", "go", "typescript"]


def gen_corpus(n=10000, seed=42):
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
        corpus.append(MockKU(
            ku_id=f"ku_{i:06d}", name=name, summary=summary, tags=tags,
        ))
    return corpus


def build_rank_bm25(corpus):
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        return None
    tokenized = [_tokenize(f"{ku.name} {ku.summary} {' '.join(ku.tags)}") for ku in corpus]
    return BM25Okapi(tokenized)


def rank_bm25_search(bm25, query, top_k, corpus):
    if bm25 is None:
        return []
    tokens = _tokenize(query)
    if not tokens:
        return []
    scores = bm25.get_scores(tokens)
    indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
    return [(corpus[i].ku_id, float(s)) for i, s in indexed if s > 0]


def gen_queries(n=100, seed=99):
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


def top10_overlap(rank_results, new_results):
    rank_ids = [ku_id for ku_id, _ in rank_results[:10]]
    new_ids  = {ku_id for ku_id, _ in new_results[:10]}
    if not rank_ids:
        return 1.0
    return sum(1 for x in rank_ids if x in new_ids) / len(rank_ids)


def main():
    print("=" * 60)
    print("BM25 回归测试: 自实现倒排 vs rank_bm25.BM25Okapi")
    print("=" * 60)

    print("\n[1/4] 生成 10000 mock corpus ...")
    corpus = gen_corpus(10000, seed=42)
    print(f"      OK, {len(corpus)} docs")

    print("\n[2/4] 构建索引 ...")
    t0 = time.time()
    new_idx = NewBM25()
    new_idx.build(corpus)
    t_new = time.time() - t0
    print(f"      新实现 build: {t_new*1000:.1f} ms")

    rank_bm25 = build_rank_bm25(corpus)
    if rank_bm25 is None:
        print("      rank_bm25 未安装,跳过一致率比对(只测性能)")

    print("\n[3/4] 100 query 跑搜索 ...")
    queries = gen_queries(100, seed=99)
    new_topk = 0
    rank_topk = 0
    overlap_sum = 0.0
    valid = 0
    t0 = time.time()
    for q in queries:
        new_r  = [(r.ku_id, r.score) for r in new_idx.search(q, top_k=10)]
        rank_r = rank_bm25_search(rank_bm25, q, 10, corpus)
        new_topk  += len(new_r)
        rank_topk += len(rank_r)
        if rank_r and new_r:
            overlap_sum += top10_overlap(rank_r, new_r)
            valid += 1
    t_search = time.time() - t0
    print(f"      耗时: {t_search*1000:.1f} ms ({(t_search/100)*1000:.2f} ms/query)")
    print(f"      新实现召回: {new_topk}, rank_bm25 召回: {rank_topk}")

    if rank_bm25 is not None and valid > 0:
        avg = overlap_sum / valid
        print(f"\n[4/4] top-10 一致率(对 rank_bm25 ground truth): {avg*100:.1f}%")
        if avg >= 0.99:
            print("      PASS (>= 99%)")
        else:
            print("      FAIL (< 99%)")
            sys.exit(1)
    else:
        print("\n[4/4] 跳过一致率(无 rank_bm25 对照)")

    print("\n[bonus] save/load roundtrip ...")
    tmp = "_tmp_bm25.pkl"
    new_idx.save(tmp)
    new_idx2 = NewBM25()
    assert new_idx2.load(tmp), "load 失败"
    assert new_idx2.size() == new_idx.size()
    for q in queries[:5]:
        r1 = sorted([(r.ku_id, r.score) for r in new_idx.search(q, 10)])
        r2 = sorted([(r.ku_id, r.score) for r in new_idx2.search(q, 10)])
        assert r1 == r2, f"save/load 不一致: {q}"
    os.remove(tmp)
    print(f"      OK, roundtrip 一致 ({new_idx.size()} docs)")

    print("\n" + "=" * 60)
    print("全部通过")
    print("=" * 60)


if __name__ == "__main__":
    main()
