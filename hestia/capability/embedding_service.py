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
"""
from __future__ import annotations
import os
import threading

_lock = threading.Lock()
_cache: dict[str, object] = {}     # model_name -> model 实例 或 None(加载失败/缺库)
_load_attempts = 0                  # 实际发起过多少次"加载尝试"(缓存命中不计),用于验证收敛


def get_text_embedder(model_name: str | None = None):
    """返回按 model_name 缓存的共享文本向量模型(sentence-transformers)。
    同名只加载一次;缺库/失败返回 None(调用方降级哈希嵌入)。线程安全。"""
    global _load_attempts
    name = (model_name or os.environ.get("HESTIA_EMBEDDING_MODEL", "all-MiniLM-L6-v2")).strip()
    with _lock:
        if name in _cache:                 # 缓存命中:绝不重复加载
            return _cache[name]
        _load_attempts += 1
        model = None
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(name)
        except Exception:
            model = None                   # 缺库 / 模型权重不在本地 / 离线拉取失败 → 降级
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
