"""检查磁盘 faiss 索引的类型和 ntotal(不依赖服务进程)"""
import sys
import os
from pathlib import Path

faiss_path = str(Path(__file__).resolve().parents[1] / "foxclaw_data" / "hkc" / "search_vector.faiss")
if len(sys.argv) > 1:
    faiss_path = sys.argv[1]

if not os.path.exists(faiss_path):
    print(f"NOT_FOUND: {faiss_path}")
    sys.exit(1)

try:
    import faiss
except ImportError:
    print("faiss-cpu not installed (try: pip install faiss-cpu)")
    sys.exit(1)

print(f"path: {faiss_path}")
print(f"size: {os.path.getsize(faiss_path)} bytes")

idx = faiss.read_index(faiss_path)
print(f"type : {type(idx).__name__}")
print(f"ntotal: {idx.ntotal}")
print(f"dim  : {idx.d}")
if hasattr(idx, "nlist"):
    print(f"nlist: {idx.nlist}")
if hasattr(idx, "metric_type"):
    print(f"metric: {idx.metric_type}")
