"""
EmbeddingService —— 进程内共享的文本向量能力(P0 性能修复)。

问题:此前每个 Harness 各建一个 SemanticDrift → 各自 `SentenceTransformer(model)`。
     5 个 Agent = 5 份同名模型驻留内存 + 5 次加载(启动慢的主因,run_all 实测 44s→7s
     还是靠离线缓存,加载次数没减)。

方案:按模型名进程内缓存,同名模型只加载一次,全系统共享同一个只读实例。
     · sentence-transformers 的 encode 是无梯度前向,多线程对同一只读模型并发 encode
       是安全的(标准的"一个模型服务多请求"模式),故共享比每 Agent 各持一份更优:
       内存 5×→1×,加载 5×→1×。
     · 缺库/加载失败 → 返回 None,调用方(SemanticDrift)自动降级 n-gram 哈希嵌入。

【P2·单 embedding 入口·share-bge-m3】
  ★ 改用 HKC 进程缓存(hkc_search.embedding_backends.get_st_model)取模型 instance,
    与 HKC 检索 / HMR VectorStore / Harness SemanticDrift 全部走同一份 instance,
    全 Foxclaw 真正只 load bge-m3 **1 次**。之前这里自己 `SentenceTransformer(name)`,
    导致 Harness 启动期独立 Loading 第 2 次(浪费 ~1GB 内存)。
"""
from __future__ import annotations
import os
import threading

_lock = threading.Lock()
_cache: dict[str, object] = {}     # model_name -> model 实例 或 None(加载失败/缺库)
_load_attempts = 0                  # 实际发起过多少次"加载尝试"(缓存命中不计),用于验证收敛


def get_text_embedder(model_name: str | None = None):
    """返回按 model_name 缓存的共享文本向量模型(sentence-transformers)。
    走 HKC 进程缓存(hkc_search.embedding_backends.get_st_model),同名只加载一次,
    与 HKC / HMR 共用同一 instance(share-bge-m3)。缺库/失败返回 None。线程安全。

    model_name 解析链(【P2·单 embedding 入口】)——
      显式传参 > EMBEDDING_MODEL(系统唯一本地入口) > HKC_EMBEDDING_MODEL > FOXCLAW_EMBEDDING_MODEL
      > all-MiniLM-L6-v2(兜底)。与 HKC init / HMR VectorStore / harness.py 完全一致,
      保证 get_st_model 缓存 key 命中同一份 instance。"""
    global _load_attempts
    name = (model_name
            or os.environ.get("EMBEDDING_MODEL")
            or os.environ.get("HKC_EMBEDDING_MODEL")
            or os.environ.get("FOXCLAW_EMBEDDING_MODEL")
            or "all-MiniLM-L6-v2").strip()
    with _lock:
        if name in _cache:                 # 本服务缓存命中(不重复走 HKC):绝不重复加载
            return _cache[name]
        _load_attempts += 1
        model = None
        try:
            # 【P2·share-bge-m3】走 HKC 共享进程缓存 —— 同名 model 全 Foxclaw 只 load 1 次
            # (HKC 检索 / HMR VectorStore / Harness SemanticDrift 共用同一份 instance)
            from hkc_search.embedding_backends import get_st_model as _hk_get
            model = _hk_get(name)
        except Exception:
            model = None                   # HKC 不可用 / 缺库 / 离线 → 降级哈希嵌入
        _cache[name] = model
        return model


def embedding_stats() -> dict:
    """可观测:当前缓存了哪些模型、发起过多少次加载。用于证明"同名只加载一次"。"""
    with _lock:
        return {
            "cached_models": {n: (m is not None) for n, m in _cache.items()},
            "distinct_models": len(_cache),
            "load_attempts": _load_attempts,
        }
