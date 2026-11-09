"""
VecRecall 适配层 — 可选记忆后端，对外接口与 `HMRAdapter` 完全一致。

为什么要这一层：业务代码只认 `HMRAdapter` 的 9 个方法（ingest / recall /
recall_by_tag / delete_memory / get_workspace / save_workspace /
save_runtime_state / restore_runtime_state / recent / stats）。
`VecRecall` 的 API 形状与之不同，直接替换会让调用方炸在一个与被调用能力无关的地方。

几处**按源码**而非按文档写的映射（v2.1 实测）：
  Foxclaw                     VecRecall 2.1
  ─────────────────────────   ───────────────────────────────────────────
  ingest(...)                 add(content, memory_type=, visibility=, owner=, ...)
  recall(query, agent_id=)    build_context(query, viewer=agent_id) 的 L2+L3
  recall_by_tag("preference") query_by_topic_key("preference")
  delete_memory(id)           delete(id)
  recent(limit)               list_memories(sort="recent", full=True)
  get_workspace(agent_id)     无对应概念 → 返回空壳(字段无人读,见 agents/base.py)

注意两点与直觉相反的地方：
1. `VecRecall.recall(memory_id)` **不是检索**，是"复习并增强记忆强度"。检索入口是
   `build_context`(带 viewer 隔离) / `search`(不做任何隔离)。用错名字会得到
   TypeError 或越权召回。
2. 隔离走 `owner` + `visibility`，在**召回前**生效 —— 这正好修掉 HMR 路径
   "全局召回 K 条再按 tag 筛"的完整性缺陷(别人的记忆挤满 top-k 时自己的召不回)。
"""
import os
import re
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from .shared_write import guard_shared_write
from typing import Any

# Foxclaw 的 type 白名单 → vecRecall 的 memory_type(episodic/semantic/procedural)。
# 分错不影响读写，但影响 vecRecall 的遗忘曲线与 procedural 精确匹配。
_FOXCLAW_TO_VR = {
    "concept": "semantic",
    "project": "semantic",
    "task": "semantic",
    "agent_memory": "semantic",
    "reflection": "semantic",
    "workflow": "procedural",
    "decision": "episodic",
    "runtime": "episodic",
    "execution": "episodic",
}
_VR_TO_FOXCLAW = {
    "semantic": "concept",
    "procedural": "workflow",
    "episodic": "concept",
}
_VALID_TYPES = set(_FOXCLAW_TO_VR)

# 偏好类记忆走 topic_key（精确查询），因为"总是注入的偏好"不该做语义模糊匹配。
_PREFERENCE_TAG = "preference"


def _ensure_vendor_importable():
    """把 vendor/ 与 vendor/hkc 加入 sys.path，使内嵌 HKC 的共享 embedding 可用。"""
    vendor = Path(__file__).resolve().parent.parent.parent / "vendor"
    for p in (vendor, vendor / "hkc"):
        if p.exists() and str(p) not in sys.path:
            sys.path.insert(0, str(p))


class _HKCSharedEmbedding:
    """与 HMR 共用同一个 SentenceTransformer 实例。

    两套后端跑同一个模型才能让影子对比有意义（维度、分数可比）；若各自
    `SentenceTransformer(...)` 一次，bge-m3 级别的模型会多占约 1GB 常驻内存。
    HKC 的 `get_st_model` 已经是进程级缓存，这里只做一层薄包装。
    """

    backend = "hkc-shared"

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None
        self._dim = None

    def _load(self):
        if self._model is None:
            _ensure_vendor_importable()
            from hkc_search.embedding_backends import get_st_model
            model = get_st_model(self.model_name)
            if model is None:
                # 不回退到直连 SentenceTransformer：那会绕过 HKC 的进程级共享缓存、
                # 多加载一份模型(bge-m3 常驻约 1GB)。失败就明确报错让工厂回退 HMR。
                raise RuntimeError(
                    f"embedding 模型不可用: {self.model_name}(HKC 共享缓存加载失败)")
            self._model = model
        return self._model

    def embed(self, text: str):
        vec = self._load().encode([text or ""], normalize_embeddings=True)[0]
        return [float(x) for x in vec]

    def embed_batch(self, texts):
        arr = self._load().encode(list(texts), normalize_embeddings=True)
        return [[float(x) for x in row] for row in arr]

    @property
    def dim(self):
        if self._dim is None:
            try:
                model = self._load()
                # ST 6.x 把 get_sentence_embedding_dimension 改名了,两个都试
                getter = (getattr(model, "get_embedding_dimension", None)
                          or getattr(model, "get_sentence_embedding_dimension", None))
                self._dim = (int(getter() or 0) or None) if getter else None
            except Exception:
                self._dim = None
        return self._dim


class VecRecallAdapter:
    """把 vecRecall v2.1 包装成 Foxclaw 期望的记忆接口。"""

    backend = "vecRecall"

    def __init__(self, storage_path: str = "./vecrecall_data", wing: str = "foxclaw",
                 use_chain: bool = False, embedding_model: str | None = None,
                 dedup: bool = True, embedding_backend=None, vector_backend=None):
        """`embedding_backend` / `vector_backend` 用于注入（测试传轻量实现）。

        默认为 None 时走生产配置：HKC 共享的 SentenceTransformer + vecRecall 默认
        的 sqlite 向量库。测试若不注入，会去加载 bge-m3 级别模型，命令长时间无输出
        —— 看起来像死锁，实际是在下载/加载权重。
        """
        from vecrecall import VecRecall

        self.wing = wing
        self.storage_path = str(storage_path)
        self.version = self._resolve_version()
        # vecRecall 本体没有任何锁（只有 REST server 层有 RLock），而它用的是
        # check_same_thread=False 的单条 sqlite 连接 + 内存 dict 索引。并行子 Agent
        # 同时 add/search 属于未定义行为，和 HMR 是同一类坑 —— 这里沿用同一套串行化。
        self._lock = threading.RLock()

        self._emb = embedding_backend or _HKCSharedEmbedding(
            self._resolve_model(embedding_model))
        self._assert_dim_matches()

        extra = {"vector_backend": vector_backend} if vector_backend is not None else {}
        self._vr = VecRecall(
            base_dir=self.storage_path,
            wing=wing,
            embedding_backend=self._emb,
            use_blockchain=use_chain,
            dedup=dedup,
            **extra,
        )

    # ── 构造辅助 ──────────────────────────────────────────
    @staticmethod
    def _resolve_version() -> str:
        try:
            import vecrecall as _pkg
            return str(getattr(_pkg, "__version__", "unknown"))
        except Exception:
            return "unknown"

    @staticmethod
    def _resolve_model(embedding_model: str | None = None) -> str:
        """与 HMRAdapter 同一条解析链，保证两套后端用同一个模型（分数与维度可比）。"""
        return (embedding_model
                or os.environ.get("EMBEDDING_MODEL")
                or os.environ.get("HKC_EMBEDDING_MODEL")
                or os.environ.get("FOXCLAW_EMBEDDING_MODEL")
                or "all-MiniLM-L6-v2")

    def _assert_dim_matches(self) -> None:
        """已有向量维度与当前嵌入维度不一致时明确失败。

        vecRecall 的 SqliteVectorBackend 与 HMR 一样用 zip() 求内积：两侧维度不同时
        短的一侧被静默截断，分数照常返回、不抛错。症状是"换了 embedding 模型之后
        召回突然变成噪声"，比直接崩更难定位。
        """
        db = Path(self.storage_path) / "vectors.db"
        if not db.exists():
            return
        import sqlite3
        try:
            conn = sqlite3.connect(str(db))
            rows = conn.execute("SELECT DISTINCT dim FROM vectors LIMIT 2").fetchall()
            conn.close()
        except Exception:
            return
        dims = {int(r[0]) for r in rows}
        want = getattr(self._emb, "dim", None)   # 轻量后端无 dim 属性时跳过
        if dims and want and dims != {int(want)}:
            raise RuntimeError(
                f"已有向量维度 {sorted(dims)} 与当前嵌入维度 {want} 不一致。"
                f"换 embedding 模型必须重建索引：删除 {self.storage_path} 后重启"
            )

    # ── 摄入 ──────────────────────────────────────────────
    _AUTO_TITLE = re.compile(r"^[A-Za-z_]+\s+\d{1,2}:\d{2}$")

    @staticmethod
    def _derive_title(content: str) -> str:
        s = str(content or "").strip()
        if not s:
            return ""
        for pfx in ("对话:", "对话：", "用户偏好:", "用户偏好：", "任务:", "任务："):
            if s.startswith(pfx):
                s = s[len(pfx):].strip()
                break
        for sep in ("→", "->"):
            if sep in s:
                s = s.split(sep, 1)[0].strip()
                break
        s = re.split(r"[。！？!?\n\r]", s, 1)[0].strip()
        return " ".join(s.split())[:80]

    def ingest(self, content: str, type: str = "concept", agent_id: str | None = None,
               title: str | None = None, tags: list | None = None, shared: bool = False, **kwargs):
        """统一摄入。签名与 `HMRAdapter.ingest` 一致。

        隔离承载：`agent_id` → owner，`shared=True` → visibility="shared"。
        不再依赖 tag 前缀做隔离，但 tag 仍然写进 metadata（前端展示与偏好查询要用）。
        """
        meta_tags = list(tags or [])
        if agent_id:
            meta_tags.append(f"agent:{agent_id}")
        if shared:
            meta_tags.append("shared")

        mtype = type if type in _VALID_TYPES else "concept"
        if not title:
            title = self._derive_title(content) or ""

        # 【P0-3】共享层写入闸门：必须在落库之前判定，语义与 HMR 路径完全一致，
        # 否则同一个策略在两个后端上表现不同。
        if shared:
            guard_shared_write(self, agent_id, content, mtype, meta_tags)

        metadata = {"tags": meta_tags, "foxclaw_type": mtype}
        if "confidence" in kwargs:
            metadata["confidence"] = kwargs["confidence"]

        # execution 是审计证据：被遗忘曲线衰减或被去重合并，就无法再证明"当时确实
        # 这么做了"。vecRecall 的 immutable 同时免于衰减与去重，正好对上这个语义。
        retention = "immutable" if mtype == "execution" else "decay"
        topic_key = kwargs.get("topic_key") or (
            _PREFERENCE_TAG if _PREFERENCE_TAG in meta_tags else "")

        with self._lock:
            return self._vr.add(
                content=content,
                wing=self.wing,
                importance=kwargs.get("importance"),
                ui_summary=(title or "")[:200],
                metadata=metadata,
                memory_type=_FOXCLAW_TO_VR.get(mtype, "semantic"),
                visibility="shared" if shared else "private",
                owner=(agent_id or "") if not shared else "",
                retention_policy=retention,
                topic_key=topic_key,
            )

    # ── 召回 ──────────────────────────────────────────────
    def _visible(self, mem, agent_id: str, include_shared: bool) -> bool:
        """可见性判定，与 vecRecall `_visible_to` 规则保持同源。

        - shared 记忆：仅当 include_shared 时可见
        - private 记忆：owner 为空(老数据)视为公开；否则必须等于当前 agent
        - agent_id 为空：不过滤（与 HMR 路径 `if agent_id:` 的行为一致）
        """
        vis = getattr(mem, "visibility", "private") or "private"
        owner = getattr(mem, "owner", "") or ""
        if not agent_id:
            return True
        if vis == "shared":
            return include_shared
        return owner == agent_id or not owner

    def recall(self, query: str = "", agent_id: str | None = None, top_k: int = 5,
               include_shared: bool = True, **kwargs):
        """统一召回，返回 list[dict]。

        走 `build_context(viewer=...)` 而不是 `search()`：前者在 L1/L2/L3 全层按
        visibility+owner 过滤，是**召回前**生效的隔离；`search()` 源码里明写
        "不经过任何结构过滤"，拿它做按 Agent 召回会越权。

        只取 L2+L3：L1 是"全库 importance top-15 + immutable 全额并入"，与当前
        query 无关，混进来会挤占 top_k。
        """
        fetch_k = min(max(top_k * 5, 20), 100) if agent_id else top_k
        results = []
        try:
            with self._lock:
                bundle = self._vr.build_context(
                    current_query=query or "",
                    load_l2=True,
                    load_l3=bool(query and query.strip()),
                    viewer=agent_id,
                )
                results = list(getattr(bundle, "l2_topic_context", []) or []) + \
                          list(getattr(bundle, "l3_deep_results", []) or [])
                if not results and (query or "").strip():
                    # L2 有 0.55 阈值：命中偏弱时整层为空。HMR 的 recall 是无条件
                    # 返回 top_k，为不改变上层"总能拿到几条"的预期，退化到全量语义
                    # 检索，再在下面用 _visible 补上隔离（search 本身不隔离）。
                    results = list(self._vr.search(query, n=fetch_k) or [])
        except Exception:
            return []

        out, seen = [], set()
        for r in results:
            mem = getattr(r, "memory", None)
            if mem is None:
                continue
            mid = getattr(mem, "id", "")
            if not mid or mid in seen:
                continue
            if not self._visible(mem, agent_id, include_shared):
                continue
            seen.add(mid)
            row = self._memory_to_row(mem)
            row["score"] = round(float(getattr(r, "score", 0.0) or 0.0), 4)
            out.append(row)

        out.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return out[:top_k]

    def _memory_to_row(self, mem) -> dict:
        """Memory → Foxclaw 的召回结果结构。"""
        meta = getattr(mem, "metadata", None) or {}
        tags = [t for t in (meta.get("tags") or []) if isinstance(t, str)]
        owner = getattr(mem, "owner", "") or ""
        if owner and f"agent:{owner}" not in tags:
            tags.append(f"agent:{owner}")
        shared = (getattr(mem, "visibility", "") == "shared")
        if shared and "shared" not in tags:
            tags.append("shared")
        ftype = (meta.get("foxclaw_type")
                 or _VR_TO_FOXCLAW.get(getattr(mem, "memory_type", ""), "concept"))
        return {
            "id": getattr(mem, "id", ""),
            "title": (getattr(mem, "ui_summary", "") or "")[:120],
            "content": getattr(mem, "content", "") or "",
            "type": ftype,
            "confidence": meta.get("confidence", 1.0),
            "tags": tags,
            "shared": shared,
        }

    def recall_by_tag(self, agent_id=None, tag="preference", limit=8, include_shared=True):
        """按 tag 精确拉取（不做语义排序），用于"总是注入"的偏好类记忆。

        vecRecall 的硬边界禁止用 wing/topic 结构过滤**检索**，但偏好本来就不该做
        语义模糊匹配 —— 它走 `query_by_topic_key`（精确、时间序），语义反而更准。
        非偏好 tag 退化为全量列表筛选，避免调用方拿到静默的空结果。
        """
        mems = []
        try:
            with self._lock:
                if tag == _PREFERENCE_TAG:
                    mems = list(self._vr.query_by_topic_key(tag, wing=self.wing) or [])
                if not mems:
                    listed = self._vr.list_memories(wing=self.wing, limit=500,
                                                    full=True, sort="recent")
                    for d in (listed.get("memories") or []):
                        t = (d.get("metadata") or {}).get("tags") or []
                        if tag in t:
                            mems.append(SimpleNamespace(
                                id=d.get("id", ""), content=d.get("content", ""),
                                metadata=d.get("metadata") or {},
                                visibility=d.get("visibility", "private"),
                                owner="", timestamp=d.get("timestamp", ""),
                                ui_summary=d.get("ui_summary", ""),
                                memory_type=d.get("memory_type", ""),
                            ))
        except Exception:
            return []

        rows = []
        for m in mems:
            if not self._visible(m, agent_id, include_shared):
                continue
            row = self._memory_to_row(m)
            row["_ts"] = getattr(m, "timestamp", "") or ""
            rows.append(row)

        rows.sort(key=lambda x: str(x.get("_ts")), reverse=True)   # 新近优先
        # 同一偏好反复表达会存成多条，去重保留最新，否则会挤满 limit
        deduped, seen_c = [], set()
        for x in rows:
            if x["content"] in seen_c:
                continue
            seen_c.add(x["content"])
            deduped.append(x)
        return deduped[:limit]

    def delete_memory(self, memory_id):
        with self._lock:
            try:
                return bool(self._vr.delete(memory_id))
            except Exception:
                return False

    # ── 展示 ──────────────────────────────────────────────
    def recent(self, limit: int = 20) -> list:
        """最近的记忆条目（新→旧），供前端右栏"记忆信息条"。"""
        rows = []
        try:
            with self._lock:
                listed = self._vr.list_memories(wing=self.wing, limit=max(1, int(limit)),
                                                full=True, sort="recent")
        except Exception:
            return []
        for d in (listed.get("memories") or []):
            meta = d.get("metadata") or {}
            tags = [t for t in (meta.get("tags") or []) if isinstance(t, str)]
            agent = ""
            for t in tags:
                if t.startswith("agent:"):
                    agent = t.split(":", 1)[1]
                    break
            title = (d.get("ui_summary") or "").strip()
            if not title or self._AUTO_TITLE.match(title):
                title = self._derive_title(d.get("content") or "") or title
            rows.append({
                "id": d.get("id", ""),
                "type": meta.get("foxclaw_type") or _VR_TO_FOXCLAW.get(
                    d.get("memory_type", ""), ""),
                "title": title[:120],
                "summary": (d.get("ui_summary") or d.get("content_preview")
                            or d.get("content") or "")[:180],
                "agent": agent,
                "shared": d.get("visibility") == "shared",
                "created_at": str(d.get("timestamp") or ""),
            })
        return rows

    # ── HMR 专有接口的占位 ────────────────────────────────
    # vecRecall 没有"会话工作区 / 运行时快照"这两个概念。留占位而不是让调用方
    # AttributeError：上层有统一的"后端接口"假设，缺方法会炸在一个与被调用能力
    # 无关的地方。agents/base.py 取到 workspace 后并不读其字段（active_goal 的
    # 写入已移除），所以空壳没有实际成本。
    def get_workspace(self, agent_id: str):
        return SimpleNamespace(agent_id=agent_id, active_goal="", status="idle",
                               push_task=lambda _t: None)

    def save_workspace(self, agent_id: str):
        return None

    def save_runtime_state(self, **kwargs):
        return None

    def restore_runtime_state(self):
        return None

    # ── 状态 ──────────────────────────────────────────────
    def get_system_status(self):
        return {"backend": self.backend, "version": self.version, "wing": self.wing}

    def stats(self) -> dict:
        """记忆统计：前端记忆中心展示真实数字。字段与 HMRAdapter.stats 一致。"""
        total = shared_count = relations = 0
        by_agent: dict[str, Any] = {}
        embedding = type(self._emb).__name__
        try:
            with self._lock:
                listed = self._vr.list_memories(wing=self.wing, limit=5000,
                                                full=True, sort="recent")
                mems = listed.get("memories") or []
                total = int(listed.get("total") or len(mems))
                for d in mems:
                    if d.get("visibility") == "shared":
                        shared_count += 1
                    for t in ((d.get("metadata") or {}).get("tags") or []):
                        if isinstance(t, str) and t.startswith("agent:"):
                            aid = t.split(":", 1)[1] or "unknown"
                            by_agent[aid] = by_agent.get(aid, 0) + 1
                g = ((self._vr.stats() or {}).get("graph") or {})
                for k in ("edges", "total_edges", "relations"):
                    if isinstance(g.get(k), int):
                        relations = g[k]
                        break
        except Exception:
            pass
        return {
            "backend": self.backend,
            "version": self.version,
            "total_memories": int(total),
            "relations": int(relations),
            "shared": int(shared_count),
            "private": int(max(total - shared_count, 0)),
            "by_agent": by_agent,
            "embedding": embedding,
        }

    def close(self):
        with self._lock:
            closer = getattr(self._vr, "close", None)
            if closer is not None:
                try:
                    closer()
                except Exception:
                    pass
