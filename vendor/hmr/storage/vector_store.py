"""
VectorStore - 语义索引与检索
修复内容：
1. 向量持久化到磁盘（重启不丢失）
2. 真实 Embedding（三层 fallback：OpenAI → sentence-transformers → TF-IDF）
3. 启动时自动从磁盘加载已有向量
"""

import json
import os
import math
import re
import hashlib
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional
from collections import Counter


# ============================================================================
# Embedding Provider（三层 fallback）
# ============================================================================

class EmbeddingProvider:
    """
    三层 fallback Embedding：
    1. OpenAI API（最好，需要 API Key）— 默认跳过（用户用云 LLM 主对话，embedding 走 bge-m3）
    2. sentence-transformers（本地模型，pip install sentence-transformers）
    3. TF-IDF 字符级 n-gram（纯 Python，始终可用，比哈希有真实语义）

    【P1·全系统 2 模型】模型解析顺序：
      显式传参 > HKC_EMBEDDING_MODEL(bge-m3)> FOXCLAW_EMBEDDING_MODEL > MiniLM 兜底。
    【P1·share-bge-m3】ST 加载优先调 HKC 共享缓存 get_st_model(),同名复用 instance,
    避免启动期重复加载 bge-m3 占双倍内存。
    """

    def __init__(self, model: str = None):
        # 【P2·单 embedding 入口】模型解析链 —— 与 harness.py / evidence.py 一致:
        # 显式 > EMBEDDING_MODEL(系统单入口) > HKC_EMBEDDING_MODEL > FOXCLAW_EMBEDDING_MODEL > MiniLM 兜底
        self.model = (model
                      or os.environ.get("EMBEDDING_MODEL")
                      or os.environ.get("HKC_EMBEDDING_MODEL")
                      or os.environ.get("FOXCLAW_EMBEDDING_MODEL")
                      or "all-MiniLM-L6-v2")
        self._provider = None
        self._vocab: Dict[str, int] = {}
        self._idf: Dict[str, float] = {}
        self._dim = 512  # TF-IDF fallback 维度
        self._lock = threading.Lock()

        self._init_provider()

    def _init_provider(self):
        # 【P1·全系统 2 模型】OpenAI 优先级:仅当用户显式开 HMR_USE_OPENAI_EMBEDDING=1 才走,
        # 否则跳过(用户主对话已用 MiniMax 云 LLM,embedding 一律 bge-m3,不再切到 OpenAI)。
        if os.environ.get("HMR_USE_OPENAI_EMBEDDING", "").strip() == "1":
            try:
                import openai
                key = os.environ.get("OPENAI_API_KEY", "")
                if key:
                    self._client = openai.OpenAI(api_key=key)
                    self._provider = "openai"
                    print("[HMR Embedding] 使用 OpenAI Embedding")
                    return
            except ImportError:
                pass
            except Exception as e:
                print(f"[HMR Embedding] OpenAI 初始化失败，降级：{e}")

        # 尝试 sentence-transformers
        # 【P1·share-bge-m3】优先用 HKC 共享缓存(同名复用 instance,启动期 0 重复加载)。
        # 注意:sentence-transformers 已安装但模型权重下载失败(离线/内网)时抛 OSError,
        # 不能只 catch ImportError,否则会直接崩溃而非降级到 TF-IDF。
        try:
            _shared = None
            try:
                from hkc_search.embedding_backends import get_st_model as _hk_get
                _shared = _hk_get(self.model)
            except Exception as _hk_e:
                # 【P2·share-bge-m3】打印根因,不再静默吞错,便于排查 sys.path / 缓存 key 问题
                print(f"[HMR-HKC 共享跳过] {_hk_e.__class__.__name__}: {_hk_e} → HMR 兜底独立 load SentenceTransformer")
            if _shared is not None:
                self._st_model = _shared
                # 用实际模型维度校正 dim(bge-m3=1024, MiniLM=384)
                _fn = (getattr(_shared, "get_embedding_dimension", None)
                       or getattr(_shared, "get_sentence_embedding_dimension", None))
                if _fn:
                    self._dim = _fn()
            else:
                from sentence_transformers import SentenceTransformer
                self._st_model = SentenceTransformer(self.model)
                _fn = (getattr(self._st_model, "get_embedding_dimension", None)
                       or getattr(self._st_model, "get_sentence_embedding_dimension", None))
                if _fn:
                    self._dim = _fn()
            self._provider = "sentence_transformers"
            print(f"[HMR Embedding] 使用 sentence-transformers ({self.model}, dim={self._dim})")
            return
        except ImportError:
            pass
        except Exception as e:
            print(f"[HMR Embedding] sentence-transformers 不可用（可能模型未缓存/离线），降级到 TF-IDF：{e}")

        # fallback: TF-IDF 字符级 n-gram（纯本地，无需任何下载，离线环境保底可用）
        self._provider = "tfidf"
        print("[HMR Embedding] 使用 TF-IDF n-gram Embedding（安装 openai 或 sentence-transformers 可获得更好效果）")

    def embed(self, text: str) -> List[float]:
        if self._provider == "openai":
            return self._embed_openai(text)
        elif self._provider == "sentence_transformers":
            return self._embed_st(text)
        else:
            return self._embed_tfidf(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        if self._provider == "openai":
            return self._embed_openai_batch(texts)
        elif self._provider == "sentence_transformers":
            return self._embed_st_batch(texts)
        else:
            return [self._embed_tfidf(t) for t in texts]

    # --- OpenAI ---

    def _embed_openai(self, text: str) -> List[float]:
        try:
            resp = self._client.embeddings.create(
                model=self.model,
                input=text[:8000]
            )
            return resp.data[0].embedding
        except Exception as e:
            print(f"[HMR Embedding] OpenAI 失败，降级到 TF-IDF: {e}")
            self._provider = "tfidf"
            return self._embed_tfidf(text)

    def _embed_openai_batch(self, texts: List[str]) -> List[List[float]]:
        try:
            resp = self._client.embeddings.create(
                model=self.model,
                input=[t[:8000] for t in texts]
            )
            return [item.embedding for item in resp.data]
        except Exception as e:
            print(f"[HMR Embedding] OpenAI 批量失败: {e}")
            return [self._embed_tfidf(t) for t in texts]

    # --- sentence-transformers ---

    def _embed_st(self, text: str) -> List[float]:
        vec = self._st_model.encode(text, normalize_embeddings=True)
        return vec.tolist()

    def _embed_st_batch(self, texts: List[str]) -> List[List[float]]:
        vecs = self._st_model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vecs]

    # --- TF-IDF 字符级 n-gram（真实语义近似）---

    def _tokenize(self, text: str) -> List[str]:
        """字符级 2-gram + 词级 1-gram 组合"""
        text = text.lower()
        words = re.findall(r'\b\w+\b', text)
        # 词级 unigram
        tokens = list(words)
        # 字符级 bigram（捕捉词根相似性）
        for word in words:
            for i in range(len(word) - 1):
                tokens.append(word[i:i+2])
        return tokens

    def _update_vocab(self, tokens: List[str]):
        with self._lock:
            for t in tokens:
                if t not in self._vocab:
                    self._vocab[t] = len(self._vocab)

    def _embed_tfidf(self, text: str) -> List[float]:
        tokens = self._tokenize(text)
        self._update_vocab(tokens)

        # TF
        tf = Counter(tokens)
        total = max(len(tokens), 1)

        # 构建向量（固定维度，用哈希分桶）
        vec = [0.0] * self._dim
        for token, count in tf.items():
            # 用 token 本身做哈希分桶（不同于纯哈希embed，这里有TF加权）
            bucket = int(hashlib.md5(token.encode()).hexdigest(), 16) % self._dim
            tf_val = count / total
            # IDF（全局文档频率近似）
            idf = self._idf.get(token, 1.0)
            vec[bucket] += tf_val * idf

        # L2 归一化
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]

        return vec

    def update_idf(self, corpus_tokens: List[List[str]], total_docs: int):
        """更新 IDF（当有足够文档时调用）"""
        doc_freq: Dict[str, int] = Counter()
        for tokens in corpus_tokens:
            for t in set(tokens):
                doc_freq[t] += 1
        self._idf = {
            t: math.log((total_docs + 1) / (freq + 1)) + 1
            for t, freq in doc_freq.items()
        }

    @property
    def dimension(self) -> int:
        if self._provider == "openai":
            return 1536
        elif self._provider == "sentence_transformers":
            return self._dim
        else:
            return self._dim


# ============================================================================
# VectorStore（持久化版本）
# ============================================================================

class VectorStore:
    """
    向量存储（持久化版本）

    修复：
    - 向量保存到磁盘（JSON 格式），重启后自动加载
    - 使用真实 Embedding（EmbeddingProvider）
    - 写入时加文件锁防止并发损坏
    """

    def __init__(
        self,
        embedding_model: str = None,    # 【P2】默认 None → EmbeddingProvider 内部按 env 链解析
        storage_path: Optional[str] = None
    ):
        self.embedding_model = (embedding_model
                                or os.environ.get("EMBEDDING_MODEL")
                                or os.environ.get("HKC_EMBEDDING_MODEL")
                                or os.environ.get("FOXCLAW_EMBEDDING_MODEL")
                                or "all-MiniLM-L6-v2")
        self.storage_path = Path(storage_path) if storage_path else None
        self._lock = threading.Lock()

        # 内存存储
        self.vectors: Dict[str, List[float]] = {}
        self.metadata: Dict[str, Dict[str, Any]] = {}

        # Embedding provider
        self.embedder = EmbeddingProvider(model=embedding_model)

        # 从磁盘加载已有向量
        if self.storage_path:
            self._load_from_disk()

    # -------------------------------------------------------------------------
    # 核心 API
    # -------------------------------------------------------------------------

    def embed(self, text: str) -> List[float]:
        """生成真实 Embedding"""
        return self.embedder.embed(text)

    def add(
        self,
        id: str,
        embedding: List[float],
        metadata: Dict[str, Any]
    ):
        """添加向量，并持久化到磁盘"""
        with self._lock:
            self.vectors[id] = embedding
            self.metadata[id] = metadata
            if self.storage_path:
                self._save_to_disk()

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """语义搜索"""
        results = []

        with self._lock:
            for vec_id, vector in self.vectors.items():
                if filters:
                    meta = self.metadata.get(vec_id, {})
                    if not self._matches_filters(meta, filters):
                        continue

                similarity = self._cosine_similarity(query_embedding, vector)
                results.append({
                    "id": vec_id,
                    "score": similarity,
                    "metadata": self.metadata.get(vec_id, {})
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def delete(self, id: str) -> bool:
        with self._lock:
            if id in self.vectors:
                del self.vectors[id]
                del self.metadata[id]
                if self.storage_path:
                    self._save_to_disk()
                return True
        return False

    def rebuild_from_memories(self, memories):
        """
        从 MemoryFS 重建向量索引（修复持久化裂缝）
        当 VectorStore 为空但 MemoryFS 有数据时调用
        """
        if not memories:
            return

        print(f"[HMR VectorStore] 从 MemoryFS 重建索引，共 {len(memories)} 条记忆...")

        texts = [m.semantic_summary or m.content[:500] for m in memories]
        embeddings = self.embedder.embed_batch(texts)

        with self._lock:
            for memory, embedding in zip(memories, embeddings):
                self.vectors[memory.id] = embedding
                self.metadata[memory.id] = {
                    "type": memory.type,
                    "title": memory.title,
                    "tags": memory.tags,
                    "temporal_weight": memory.temporal_weight
                }
            if self.storage_path:
                self._save_to_disk()

        print(f"[HMR VectorStore] 重建完成")

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_vectors": len(self.vectors),
            "embedding_provider": self.embedder._provider,
            "embedding_dimension": self.embedder.dimension,
            "memory_mb": self._estimate_memory_mb(),
            "persisted": self.storage_path is not None
        }

    # -------------------------------------------------------------------------
    # 持久化
    # -------------------------------------------------------------------------

    def _save_to_disk(self):
        """保存向量到磁盘（JSON 格式，线程安全内已调用）"""
        if not self.storage_path:
            return

        self.storage_path.mkdir(parents=True, exist_ok=True)
        vec_path = self.storage_path / "vectors.json"
        meta_path = self.storage_path / "vector_metadata.json"

        # 写临时文件再原子替换，防止写到一半崩溃
        tmp_vec = vec_path.with_suffix(".tmp")
        tmp_meta = meta_path.with_suffix(".tmp")

        with open(tmp_vec, "w") as f:
            json.dump(self.vectors, f)
        with open(tmp_meta, "w") as f:
            json.dump(self.metadata, f, ensure_ascii=False, indent=2)

        tmp_vec.replace(vec_path)
        tmp_meta.replace(meta_path)

    def _load_from_disk(self):
        """启动时从磁盘加载向量"""
        if not self.storage_path:
            return

        vec_path = self.storage_path / "vectors.json"
        meta_path = self.storage_path / "vector_metadata.json"

        if vec_path.exists() and meta_path.exists():
            try:
                with open(vec_path, "r") as f:
                    self.vectors = json.load(f)
                with open(meta_path, "r") as f:
                    self.metadata = json.load(f)
                print(f"[HMR VectorStore] 从磁盘加载 {len(self.vectors)} 条向量")
            except Exception as e:
                print(f"[HMR VectorStore] 加载失败，从空开始: {e}")
                self.vectors = {}
                self.metadata = {}

    # -------------------------------------------------------------------------
    # 工具方法
    # -------------------------------------------------------------------------

    @staticmethod
    def _cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
        if len(vec1) != len(vec2):
            return 0.0
        dot = sum(a * b for a, b in zip(vec1, vec2))
        n1 = math.sqrt(sum(x * x for x in vec1))
        n2 = math.sqrt(sum(x * x for x in vec2))
        if n1 == 0 or n2 == 0:
            return 0.0
        return dot / (n1 * n2)

    @staticmethod
    def _matches_filters(metadata: Dict[str, Any], filters: Dict[str, Any]) -> bool:
        for key, value in filters.items():
            if key not in metadata:
                return False
            meta_val = metadata[key]
            if isinstance(value, list):
                if meta_val not in value:
                    return False
            else:
                if meta_val != value:
                    return False
        return True

    def _estimate_memory_mb(self) -> float:
        n = len(self.vectors)
        dim = self.embedder.dimension
        return round(n * dim * 4 / (1024 * 1024), 2)
