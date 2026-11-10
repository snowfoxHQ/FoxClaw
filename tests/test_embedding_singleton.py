"""
EmbeddingService 单例(P0 性能修复)测试 —— 全离线,不加载真实重模型。

用一个**不存在的模型名** + HF_HUB_OFFLINE,让 sentence-transformers 加载快速失败,
从而无需真的载入 7s 的模型,即可确定性地证明:
  1. 同名模型只发起一次加载(缓存命中不再加载)
  2. 5 个 SemanticDrift 用同名 → 只加载 1 次(此前是 5 次,即本次 P0 收敛的核心)
  3. 不同模型名 → 各自一次
  4. 加载失败时 SemanticDrift 优雅降级 hash 模式,encode/score 行为不变
"""
import os, sys
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.capability import get_text_embedder, embedding_stats
from foxclaw.harness.harness import SemanticDrift

BOGUS = "foxclaw-nonexistent-model-xyz"
BOGUS2 = "foxclaw-nonexistent-model-abc"


def test_same_name_loads_once():
    print("\n【1】同名模型只加载一次(缓存命中不重复加载)")
    before = embedding_stats()["load_attempts"]
    a = get_text_embedder(BOGUS)
    b = get_text_embedder(BOGUS)
    c = get_text_embedder(BOGUS)
    after = embedding_stats()["load_attempts"]
    assert after - before == 1, f"同名 3 次调用应只加载 1 次,实际 {after - before}"
    assert a is b is c, "同名应返回同一个缓存对象(此处均为 None,加载失败)"
    print("  ✓ 同名调用 3 次 → 加载尝试仅 +1")


def test_five_harnesses_one_load():
    print("\n【2】5 个 SemanticDrift 用同名 → 只加载 1 次(P0 收敛核心)")
    name = "foxclaw-nonexistent-shared"
    before = embedding_stats()["load_attempts"]
    drifts = [SemanticDrift(name) for _ in range(5)]
    after = embedding_stats()["load_attempts"]
    assert after - before == 1, f"5 个 SemanticDrift 同名应只加载 1 次,实际 {after - before}(修复前=5)"
    assert all(d._model is drifts[0]._model for d in drifts), "5 个应共享同一模型引用"
    print("  ✓ 5 个 SemanticDrift(同名) → 加载尝试仅 +1(修复前为 5)")


def test_distinct_names_separate():
    print("\n【3】不同模型名各自加载一次")
    before = embedding_stats()["load_attempts"]
    get_text_embedder(BOGUS2)
    get_text_embedder(BOGUS2)
    after = embedding_stats()["load_attempts"]
    assert after - before == 1
    st = embedding_stats()
    assert st["distinct_models"] >= 2
    print(f"  ✓ 不同名各加载一次;当前缓存 {st['distinct_models']} 个模型")


def test_graceful_hash_fallback():
    print("\n【4】加载失败 → SemanticDrift 降级 hash,行为不变")
    import numpy as np
    d = SemanticDrift(BOGUS)
    assert d.mode == "hash", "bogus 模型加载失败应降级 hash"
    v = d.encode("活字印刷术由毕昇发明")
    assert isinstance(v, np.ndarray) and v.shape[0] == SemanticDrift.DIM
    s_self = d.score("活字印刷术由毕昇发明", d.encode("活字印刷术由毕昇发明"))
    s_none = d.score("任意", None)
    assert 0.0 <= s_self <= 0.05 and s_none == 0.0
    print(f"  ✓ hash 模式 encode/score 正常(自比漂移={s_self:.3f}, 空基线={s_none})")


def main():
    print("=" * 60)
    print("EmbeddingService 单例 · P0 性能修复(加载 5×→1×)")
    print("=" * 60)
    test_same_name_loads_once()
    test_five_harnesses_one_load()
    test_distinct_names_separate()
    test_graceful_hash_fallback()
    print(f"\n  最终缓存态: {embedding_stats()}")
    print("\n" + "=" * 60)
    print("✓ P0 验证完成:同名模型进程内只加载一次,5 Harness 共享 1 份")
    print("=" * 60)


if __name__ == "__main__":
    main()
