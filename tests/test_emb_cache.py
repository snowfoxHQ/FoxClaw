"""检索/embedding 层共享向量缓存:批量只编未命中、跨层共享、LRU 有界、锁外编码、线程安全。
用假模型(计数编码次数)证明机制,不依赖真模型 → 全离线确定性。"""
import os, sys, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "vendor" / "hkc"))


def test_emb_cache():
    os.environ["HKC_EMB_CACHE_MAX"] = "50"
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

    import numpy as np
    from hkc_search.embedding_backends import (encode_cached, emb_cache_stats,
                                               emb_cache_clear, _VEC_MAX)

    print("=" * 60); print("共享向量缓存验证"); print("=" * 60)

    class FakeModel:
        """假编码器:记录被真正编码过的文本(用来证明"没重复编码")。"""
        def __init__(self): self.encoded = []
        def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
            self.encoded.extend(texts)
            return np.asarray([[float(len(t)), float(hash(t) % 97), 1.0] for t in texts],
                              dtype=np.float32)

    emb_cache_clear(); m = FakeModel()
    v1 = encode_cached(m, "fake", ["hello"])
    v2 = encode_cached(m, "fake", ["hello"])
    assert m.encoded == ["hello"], f"同文本应只编一次: {m.encoded}"
    assert np.array_equal(v1, v2), "命中应返回同一向量"
    print("[1] 同文本只编一次(命中不重编)OK")

    m2 = FakeModel(); emb_cache_clear()
    encode_cached(m2, "fake", ["a", "b"])
    encode_cached(m2, "fake", ["a", "b", "c", "d"])
    assert m2.encoded == ["a", "b", "c", "d"], f"只该补编 c,d: {m2.encoded}"
    print("[2] 批量只编未命中(a,b 命中 → 只补编 c,d)OK")

    m3 = FakeModel(); emb_cache_clear()
    first = encode_cached(m3, "fake", ["x", "y", "z"])
    mixed = encode_cached(m3, "fake", ["z", "x", "y"])
    assert np.array_equal(mixed[0], first[2]) and np.array_equal(mixed[1], first[0]), "顺序错乱"
    print("[3] 混排全命中仍按入参顺序返回 OK")

    m4 = FakeModel(); emb_cache_clear()
    encode_cached(m4, "modelA", ["same"])
    encode_cached(m4, "modelB", ["same"])
    assert m4.encoded == ["same", "same"], "不同模型不应共用向量"
    print("[4] 模型名隔离(同文本不同模型各编各的)OK")

    m5 = FakeModel(); emb_cache_clear()
    r = encode_cached(m5, "fake", ["mut"]); r[0][0] = 999.0
    again = encode_cached(m5, "fake", ["mut"])
    assert again[0][0] != 999.0, "调用方改结果不该污染缓存"
    print("[5] 返回副本(改结果不污染缓存)OK")

    m6 = FakeModel(); emb_cache_clear()
    for i in range(_VEC_MAX + 30):
        encode_cached(m6, "fake", [f"t{i}"])
    st = emb_cache_stats()
    assert st["size"] <= _VEC_MAX, f"缓存未封顶: {st}"
    print(f"[6] LRU 封顶({st['size']} ≤ {_VEC_MAX},防长跑内存膨胀)OK")

    emb_cache_clear(); m7 = FakeModel()
    encode_cached(m7, "fake", ["p", "q"])
    encode_cached(m7, "fake", ["p", "q"])
    s = emb_cache_stats()
    assert s["hits"] == 2 and s["misses"] == 2 and s["hit_rate"] == 0.5, s
    print(f"[7] 命中率统计(hits={s['hits']} misses={s['misses']} rate={s['hit_rate']})OK")

    emb_cache_clear(); m8 = FakeModel(); errs = []
    def worker():
        try:
            for _ in range(20):
                encode_cached(m8, "fake", ["c1", "c2", "c3"])
        except Exception as e:
            errs.append(e)
    ths = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in ths]; [t.join() for t in ths]
    assert not errs, errs
    assert emb_cache_stats()["size"] == 3, emb_cache_stats()
    print("[8] 并发 6 线程 × 20 轮:无异常、缓存收敛为 3 条 OK")

    emb_cache_clear()
    from hkc_kee.kee import _encode_one
    m9 = FakeModel()
    _encode_one(m9, "shared", "同一句话")
    encode_cached(m9, "shared", ["同一句话"])
    assert m9.encoded == ["同一句话"], f"KEE 与检索应共用缓存,只编一次: {m9.encoded}"
    print("[9] 跨层共享(KEE _encode_one 与检索共用一份,只编一次)OK")

    print("\n共享向量缓存验证全部通过")


if __name__ == "__main__":
    test_emb_cache()
