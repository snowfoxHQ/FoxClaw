"""
hkc-search / vector_search.py
向量检索（Warm 层）。

向量生成委托给可插拔的 EmbeddingBackend（见 embedding_backends.py）：
  LocalSTBackend  本地 sentence-transformers
  TEIBackend      远程 TEI 服务
  StubBackend     离线/测试

FAISS IndexFlatIP 做近似最近邻，向量已 L2 归一化，内积 = 余弦相似度。
VectorIndex 不再关心模型如何加载，只调用 backend.encode()。

阶段 A:manifest 升级
────────────────────
旧 manifest(v1,无版本号)只存 `ku_ids / names / summaries / backend`,隐患:
  1. 加载时不校验 backend/dim 与实际后端是否匹配 → 旧 faiss 向量配新 backend 会
     让检索结果全错(向量空间不同)。
  2. 不存文本 hash → 阶段 B update_ku 无法知道"内容是否真变",每条都重建向量
     (5000 条 × 重新 encode 一次 ≈ 几十秒)。
  3. 不存 dim → 切换后端后 faiss 向量维度可能与新后端 dim 不一致,加载即崩。

新 manifest(v2):
  format_version: int (当前 2)
  backend:        str (后端名,如 "stub"/"local:bge-m3"/"tei:xxx")
  dim:            int (后端向量维度,加索引文件维度)
  ku_ids:         list[str]
  names:          list[str]
  summaries:      list[str]
  text_hashes:    list[str]  ← 阶段 B 用,长度 == len(ku_ids)

加载 8 步校验:任何一步不过 → _reset() 清空,让上层走兜底(全量重建)。
"""
from __future__ import annotations
import hashlib
import logging
import os
import pickle
from dataclasses import dataclass


from .embedding_backends import EmbeddingBackend, make_backend

logger = logging.getLogger(__name__)


# ── manifest 常量 ─────────────────────────────────────────────────────
# 升级 manifest 格式时务必 +1,旧 manifest 在 load() 校验会失败→ 触发 _reset() → 兜底全量重建
_MANIFEST_FORMAT_VERSION = 2
# _ku_text() 的语义哈希:任何对 name/summary/tags/statement 的拼接逻辑修改,都会改
# text 拼接结果 → 旧 hash 必不匹配 → 阶段 B update_ku 会按"内容变了"重 encode,不会出错。
# 但若你**同时**改了 text 拼接 + 升级了 manifest version,二者会叠加;推荐:只改其一。
_KU_TEXT_FORMAT_VERSION = 1


def _hash_text(text: str) -> str:
    """稳定的内容指纹:阶段 B 用它做 'update_ku 实际是否真改了内容' 检测。
    SHA1 16 字符足够防误判(碰撞概率 ~1/2^64);hex 短,存盘小。
    故意不用 blake2 —— _compact 的内存清洗会让 hash 形式不可读,SHA1 hex 短更易排查。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class VectorResult:
    ku_id:   str
    score:   float    # 余弦相似度 0-1
    name:    str
    summary: str


class VectorIndex:

    def __init__(
        self,
        backend: EmbeddingBackend | None = None,
        model_name: str | None = None,   # 向后兼容旧调用
    ):
        """
        backend: 显式传入 embedding 后端。
        不传则用 make_backend("auto")（本地优先，回退 stub）。
        model_name: 兼容旧接口，等价于 make_backend("local", model_name=...)。
        """
        if backend is not None:
            self._backend = backend
        elif model_name is not None:
            self._backend = make_backend("local", model_name=model_name)
        else:
            self._backend = make_backend("auto")

        self._index = None
        self._ku_ids:    list[str] = []
        self._names:     list[str] = []
        self._summaries: list[str] = []
        # 阶段 A:每条 KU 对应一个文本 hash(由 _ku_text(ku) 计算),与 _ku_ids 一一对应。
        # 阶段 B 的 update_ku 用它做"内容真伪变化"检测 —— 不变则跳过 encode,变才重编。
        # save/load 都要带;load 缺失则视为旧 v1 manifest,触发 _reset() 兜底。
        self._text_hashes: list[str] = []

    @property
    def backend_name(self) -> str:
        return self._backend.name

    @property
    def dim(self) -> int:
        return self._backend.dim

    # ── 构建 ──────────────────────────────────────────────────

    def build(self, kus: list) -> None:
        if not kus:
            return
        texts      = [self._ku_text(ku) for ku in kus]
        embeddings = self._backend.encode(texts)
        self._init_index()
        self._index.add(embeddings)
        self._ku_ids     = [ku.ku_id         for ku in kus]
        self._names      = [ku.name          for ku in kus]
        self._summaries  = [ku.summary[:100] for ku in kus]
        self._text_hashes = [_hash_text(t)    for t in texts]
        logger.info("向量索引构建完成，共 %d 条 (backend=%s)",
                    len(self._ku_ids), self._backend.name)

    def append_ku(self, ku) -> None:
        # 先 encode（触发后端维度校正），再用校正后的 dim 初始化索引
        text  = self._ku_text(ku)
        vec   = self._backend.encode([text])
        if self._index is None:
            self._init_index()
        self._index.add(vec)
        self._ku_ids.append(ku.ku_id)
        self._names.append(ku.name)
        self._summaries.append(ku.summary[:100])
        self._text_hashes.append(_hash_text(text))

    def append_bulk(self, kus: list) -> None:
        """批量追加 KU（一次 encode 调用，比循环 append_ku 快 ~10x）。
        用于启动时把磁盘索引中缺失的 KU 增量补齐：N=几百到几千时秒级返回。
        空列表直接返回。无内部锁——调用方（hybrid._lock）持有锁。"""
        if not kus:
            return
        texts      = [self._ku_text(ku) for ku in kus]
        embeddings = self._backend.encode(texts)
        if self._index is None:
            self._init_index()
        self._index.add(embeddings)
        self._ku_ids     .extend([ku.ku_id         for ku in kus])
        self._names      .extend([ku.name          for ku in kus])
        self._summaries  .extend([ku.summary[:100] for ku in kus])
        self._text_hashes.extend([_hash_text(t)     for t in texts])

    # ── 检索 ──────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 10) -> list[VectorResult]:
        if self._index is None or self._index.ntotal == 0:
            return []
        vec = self._backend.encode([query])
        k   = min(top_k, self._index.ntotal)
        distances, indices = self._index.search(vec, k)

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(self._ku_ids):
                continue
            score = float(dist)
            if score < 0.1:
                continue
            results.append(VectorResult(
                ku_id   = self._ku_ids[idx],
                score   = score,
                name    = self._names[idx],
                summary = self._summaries[idx],
            ))
        return results

    def size(self) -> int:
        return self._index.ntotal if self._index else 0

    # ── 持久化 ────────────────────────────────────────────────

    def save(self, path: str) -> None:
        import faiss
        if self._index is None:
            logger.warning("索引为空，跳过保存")
            return
        faiss.write_index(self._index, path + ".faiss")
        # 阶段 A:manifest 升 v2,加 format_version/dim/text_hashes。
        # 4 个 list 长度必须一致,save 前用 _check_invariant 校验,失败抛 AssertionError
        # —— 不让不一致的 manifest 落盘(下次加载必 8 步校验失败→ _reset 兜底,徒增重建)。
        self._check_invariant()
        with open(path + ".meta", "wb") as f:
            pickle.dump({
                "format_version": _MANIFEST_FORMAT_VERSION,
                "ku_text_version": _KU_TEXT_FORMAT_VERSION,
                "backend":        self._backend.name,
                "dim":            self._backend.dim,
                "ku_ids":         self._ku_ids,
                "names":          self._names,
                "summaries":      self._summaries,
                "text_hashes":    self._text_hashes,
            }, f)
        logger.info("向量索引已保存: %s (v%d, %d 条, dim=%d)",
                    path, _MANIFEST_FORMAT_VERSION,
                    len(self._ku_ids), self._backend.dim)

    def load(self, path: str) -> bool:
        """加载磁盘索引。返回 True 表示成功,False 表示文件不存在 / 校验失败(_reset 已清空)。"""
        import faiss
        faiss_path = path + ".faiss"
        meta_path  = path + ".meta"
        if not (os.path.exists(faiss_path) and os.path.exists(meta_path)):
            return False
        try:
            with open(meta_path, "rb") as f:
                meta = pickle.load(f)
        except Exception as e:
            logger.warning("meta 损坏无法加载,触发 _reset 兜底: %s", e)
            self._reset()
            return False
        # 8 步校验:任一不过 → _reset() → 上层走兜底全量重建
        try:
            self._validate_manifest(meta, faiss_path)
        except _ManifestValidationError as e:
            logger.warning("manifest 校验失败[%s],触发 _reset 兜底: %s",
                           e.reason, e)
            self._reset()
            return False
        # 全部通过:加载
        self._index     = faiss.read_index(faiss_path)
        self._ku_ids    = meta["ku_ids"]
        self._names     = meta["names"]
        self._summaries = meta["summaries"]
        self._text_hashes = meta["text_hashes"]
        logger.info("向量索引已加载: %d 条 (v%d, backend=%s, dim=%d)",
                    self._index.ntotal, meta.get("format_version", 1),
                    meta.get("backend"), meta.get("dim"))
        return True

    def _validate_manifest(self, meta: dict, faiss_path: str) -> None:
        """阶段 A 8 步校验。任一不通过抛 _ManifestValidationError,load() 捕获后 _reset()。

        8 步:
          1. format_version 存在且 == _MANIFEST_FORMAT_VERSION(旧 v1 无此字段 → 不过)
          2. ku_text_version 存在且 == _KU_TEXT_FORMAT_VERSION(_ku_text 拼接改了就过不了)
          3. backend 字段存在(str)
          4. dim 字段存在(int)
          5. len(ku_ids) == len(names) == len(summaries) == len(text_hashes)
          6. 索引文件 faiss 存在(已在 caller 校验)
          7. faiss 索引的 d == meta["dim"]
          8. faiss 索引 ntotal == len(meta["ku_ids"])
        """
        if not isinstance(meta.get("format_version"), int):
            raise _ManifestValidationError("missing_format_version",
                                           "format_version 字段缺失或非 int(旧 v1 manifest)")
        if meta["format_version"] != _MANIFEST_FORMAT_VERSION:
            raise _ManifestValidationError(
                "format_version_mismatch",
                f"format_version={meta['format_version']} 但当前期望 {_MANIFEST_FORMAT_VERSION}",
            )
        if meta.get("ku_text_version") != _KU_TEXT_FORMAT_VERSION:
            raise _ManifestValidationError(
                "ku_text_version_mismatch",
                f"ku_text_version={meta.get('ku_text_version')!r} 期望 {_KU_TEXT_FORMAT_VERSION}"
                f"(_ku_text 拼接逻辑变了,旧 hash 无意义,必须全量重建)",
            )
        if not isinstance(meta.get("backend"), str):
            raise _ManifestValidationError("missing_backend", "backend 字段缺失或非 str")
        if not isinstance(meta.get("dim"), int) or meta["dim"] <= 0:
            raise _ManifestValidationError("missing_dim", "dim 字段缺失或非法")
        lens = (len(meta.get("ku_ids", [])),
                len(meta.get("names", [])),
                len(meta.get("summaries", [])),
                len(meta.get("text_hashes", [])))
        if len(set(lens)) != 1:
            raise _ManifestValidationError(
                "list_length_mismatch",
                f"4 个 list 长度不一致: {lens}",
            )
        # 6/7/8 涉及 faiss 实际维度
        import faiss
        idx = faiss.read_index(faiss_path)
        if idx.d != meta["dim"]:
            raise _ManifestValidationError(
                "faiss_dim_mismatch",
                f"faiss d={idx.d} 但 meta dim={meta['dim']}",
            )
        if idx.ntotal != lens[0]:
            raise _ManifestValidationError(
                "faiss_ntotal_mismatch",
                f"faiss ntotal={idx.ntotal} 但 meta ku_ids 数={lens[0]}",
            )

    def _reset(self) -> None:
        """加载失败兜底:清空所有 in-memory 状态,让上层走全量重建。
        不删磁盘文件 —— 排查时常常需要看「旧 manifest 究竟哪里坏了」;上层兜底重建后
        会写新 manifest 把旧 .meta 覆盖。"""
        self._index       = None
        self._ku_ids      = []
        self._names       = []
        self._summaries   = []
        self._text_hashes = []
        logger.warning("VectorIndex _reset: in-memory 索引已清空,等待上层兜底重建")

    def _check_invariant(self) -> None:
        """save 前的内部一致性:5 个 list 长度 + dim 已初始化。"""
        if self._index is None:
            raise AssertionError("save() 时 _index 还没初始化")
        n = self._index.ntotal
        for name, lst in (("ku_ids",    self._ku_ids),
                          ("names",     self._names),
                          ("summaries", self._summaries),
                          ("text_hashes", self._text_hashes)):
            if len(lst) != n:
                raise AssertionError(
                    f"save() invariant 失败:{name} len={len(lst)} 但 faiss ntotal={n}"
                )

    # ── 内部方法 ─────────────────────────────────────────────

    def _init_index(self) -> None:
        try:
            import faiss
            self._index = faiss.IndexFlatIP(self._backend.dim)
        except ImportError:
            raise ImportError("faiss-cpu 未安装: pip install faiss-cpu")

    @staticmethod
    def _ku_text(ku) -> str:
        parts = [ku.name]
        if ku.summary:
            parts.append(ku.summary)
        if ku.tags:
            parts.append(" ".join(ku.tags))
        stmt = getattr(ku, "statement", "")
        if stmt:
            parts.append(stmt)
        return " ".join(parts)


class _ManifestValidationError(Exception):
    """load() 8 步校验失败的私有异常类。reason 是给 logger 用的机器可读码。"""
    def __init__(self, reason: str, msg: str):
        super().__init__(msg)
        self.reason = reason
