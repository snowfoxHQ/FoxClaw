"""检查 vector 索引状态 + 距离自动升级还差多少。

用法:
    python tools/ivf_status.py

输出:
    vector_size, bm25_size, index_state, pending_ops
    距 HKC_VECTOR_IVF_THRESHOLD 阈值还差多少 KU
    下次升级触发时日志关键词

可调环境变量:
    HKC_VECTOR_IVF_THRESHOLD  默认 10000(对应 .env 里的设定)
    HKC_STATS_URL              默认 http://127.0.0.1:8000/stats
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

THRESHOLD  = int(os.environ.get("HKC_VECTOR_IVF_THRESHOLD", "10000"))
STATS_URL  = os.environ.get("HKC_STATS_URL", "http://127.0.0.1:8000/stats")
UPGRADE_KEYWORD = "VectorIndex 升级: Flat → IVF"


def fetch_stats(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())


def main() -> int:
    try:
        s = fetch_stats(STATS_URL)
    except Exception as e:
        print(f"[ERROR] 拉 {STATS_URL} 失败: {e}")
        return 1

    vs       = s.get("vector_size", 0)
    bs       = s.get("bm25_size", 0)
    ib       = s.get("index_build", {}) or {}
    state    = ib.get("index_state", "?")
    pending  = s.get("pending_ops", 0)
    task_id  = ib.get("task_id", "-")

    remaining = max(0, THRESHOLD - vs)
    pct       = min(100.0, vs / THRESHOLD * 100) if THRESHOLD > 0 else 100.0

    print("=" * 50)
    print(f"vector_size  : {vs}")
    print(f"bm25_size    : {bs}")
    print(f"index_state  : {state}    task_id={task_id}")
    print(f"pending_ops  : {pending}")
    print(f"threshold    : {THRESHOLD}")
    print(f"progress     : {pct:5.1f}%  (距升级还差 {remaining} KU)")
    print("=" * 50)

    if THRESHOLD <= 0:
        print("阶段: Flat (升级禁用,env HKC_VECTOR_IVF_THRESHOLD=0)")
    elif vs < THRESHOLD:
        print(f"阶段: Flat (IndexFlatIP, O(N) 暴力精确)")
        print(f"下次 vector_size 跨过 {THRESHOLD} 时 append_ku 会触发升级")
        print(f"触发后看日志:    grep '{UPGRADE_KEYWORD}' <foxclaw.log>")
    else:
        print(f"阶段: IVF (IndexIVFFlat, O(√N) 倒排)")
        print(f"升级日志关键词:  '{UPGRADE_KEYWORD}'")

    return 0


if __name__ == "__main__":
    sys.exit(main())
