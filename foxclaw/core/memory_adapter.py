"""
HMR 适配层 — 把 Foxclaw 的统一调用接口翻译成 HMR v2.0 的真实 API。

接口差异:
  Foxclaw 调用                          HMR v2.0 真实 API
  ─────────────────────────────────  ──────────────────────────────────
  ingest(content, type=, agent_id=)   ingest(content, memory_type=, metadata=)
  recall(query, agent_id=, top_k=)    recall(query, top_k=) -> RecallResult
  get_workspace(agent_id)             get_workspace(agent_id) -> AgentWorkspace

Agent 隔离:HMR v2.0 的 ingest/recall 无 agent_id 概念,
适配层用 metadata.tags 注入 `agent:<id>` 标签实现按 Agent 区隔,
recall 后按标签过滤,保证五个 Agent 记忆互不污染。
"""

import os
import re
import sys
import threading
from pathlib import Path

from .shared_write import guard_shared_write
from .sensitive_write import guard_sensitive_write
from types import SimpleNamespace
from typing import Any


def _ensure_hmr_importable():
    """把 vendor/ 加入 sys.path,使内嵌的 HMR 可被导入。
    同时把 vendor/hkc 也加入,使 HMR 内部能 `from hkc_search.embedding_backends import get_st_model`
    走 HKC 进程缓存(同名 instance 只 load 1 次)—— 否则会兜底自己 SentenceTransformer Loading 第 2 次。
    """
    vendor = Path(__file__).resolve().parent.parent.parent / "vendor"
    if vendor.exists() and str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    vendor_hkc = vendor / "hkc"
    if vendor_hkc.exists() and str(vendor_hkc) not in sys.path:
        sys.path.insert(0, str(vendor_hkc))


class HMRAdapter:
    """
    包装真实 HMR v2.0,对外暴露 Foxclaw 期望的统一接口。
    若 HMR 不可用,调用方应回退到 MockHMR。
    """

    def __init__(self, storage_path: str = "./hmr_data", llm_api_key: str | None = None,
                 embedding_model: str | None = None):
        """【P1·share-bge-m3】新增 embedding_model 参数。
        上层(Foxclaw)通常在 HKC 已装配后再调本构造,bge-m3 已入 HKC 缓存;
        传具体模型名(如 HKC_EMBEDDING_MODEL)使 HMR 与 HKC 共享同一 SentenceTransformer 实例。
        不传则走 vendor/hmr/storage/vector_store.py 的 env 解析链(HKC_EMBEDDING_MODEL>bge-m3)。"""
        _ensure_hmr_importable()
        from hmr.core.hmr import HMR

        import os as _os
        resolved_model = (embedding_model
                          or _os.environ.get("EMBEDDING_MODEL")
                          or _os.environ.get("HKC_EMBEDDING_MODEL")
                          or _os.environ.get("FOXCLAW_EMBEDDING_MODEL")
                          or "all-MiniLM-L6-v2")
        self._hmr = HMR(storage_path=storage_path, llm_api_key=llm_api_key,
                        embedding_model=resolved_model)
        self.backend = "HMR"
        self.version = getattr(self._hmr, "VERSION", "2.0.0")
        self._lock = threading.RLock()

    _AUTO_TITLE = re.compile(r"^[A-Za-z_]+\s+\d{1,2}:\d{2}$")

    @staticmethod
    def _derive_title(content: str) -> str:
        """从内容提炼可读标题:剥前缀标签 → 取"问"侧 → 截到首个句读 → 限长。"""
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
        return " ".join(s.split())[:24]

    def ingest(self, content: str, type: str = "concept", agent_id: str | None = None,
               title: str | None = None, tags: list | None = None, shared: bool = False,
               consent: bool = False, **kwargs):
        """统一摄入接口。agent_id 写入 tags 实现隔离;shared=True 则写入共享层。
        未给 title 时从内容提炼一个可读标题(否则 HMR 会生成纯时间戳标题)。

        consent=True 表示"用户已对该条高敏感内容点头",跳过敏感闸门。唯一允许的调用方是
        `FoxclawOS.approve_sensitive_write`(与 PolicyGate 的 confirmed=True 同一模式):
        别处传 True 等于绕过同意流程。"""
        meta_tags = list(tags or [])
        if agent_id:
            meta_tags.append(f"agent:{agent_id}")
        if shared:
            meta_tags.append("shared")
        metadata = {"tags": meta_tags}
        if "confidence" in kwargs:
            metadata["confidence"] = kwargs["confidence"]
        valid = {"concept", "project", "decision", "runtime", "task",
                 "workflow", "agent_memory", "execution", "reflection"}
        mtype = type if type in valid else "concept"
        if not title:
            title = self._derive_title(content) or None
        if shared:
            guard_shared_write(self, agent_id, content, mtype, meta_tags)
        if not consent:
            blocked = guard_sensitive_write(self, agent_id, content, mtype)
            if blocked is not None and not blocked.allowed:
                # 高敏感内容不落库:进待确认队列,由用户同意后再写(见 core/sensitive_write.py)
                return SimpleNamespace(id="", type=mtype, pending=True,
                                       pending_id=blocked.pending_id, reason=blocked.reason,
                                       categories=list(blocked.categories))
        with self._lock:
            return self._hmr.ingest(content, memory_type=mtype, title=title, metadata=metadata)

    def recall(self, query: str = "", agent_id: str | None = None, top_k: int = 5,
               include_shared: bool = True, **kwargs):
        """统一召回接口。返回 list[dict]。
        默认召回:本 Agent 的记忆 + 共享层(shared)记忆;include_shared=False 只看自己。

        M4 根治(隔离召回完整性):优先走 tag 预过滤路径 —— 先把候选池缩到"本 Agent + shared"
        的**全集**,再在这个子集内按语义相似度排序取 top_k。这样本 Agent 的记忆一定进入排序,
        不会因"库中其他 Agent 有大量强相关记忆挤占全局 top-k"而被漏掉(老路径的完整性缺陷)。
        底层 HMR 结构不可用(如 MockHMR / 版本差异)时,回退到老的"全局召回再过滤"路径。
        隔离性两条路径都成立:只有带 agent:<id> 或 shared 标签的记忆才会返回。"""
        filtered = self._filtered_recall(query, agent_id, top_k, include_shared)
        if filtered is not None:
            return filtered
        return self._legacy_recall(query, agent_id, top_k, include_shared)

    def recall_by_tag(self, agent_id=None, tag="preference", limit=8, include_shared=True):
        """按 **tag** 拉记忆(**不做语义排序**)——用于"总是注入"的偏好类记忆:不管当前 query 语义,
        只要带该 tag 且属本 Agent + shared,就按新近取近 limit 条。这正是普通语义 recall 做不到的
        (语义不相近就召不回),是"偏好优先召回"的核心。**用 tag 而非 type**:tag 是自由 metadata、
        不受 `ingest` 的 memory_type 白名单限制(preference 不在白名单会被降级 concept)。结构不可用则 []。"""
        fs = getattr(self._hmr, "memory_fs", None)
        if fs is None or not hasattr(fs, "list_memories"):
            return []
        try:
            with self._lock:
                out = []
                for m in fs.list_memories():
                    tags = getattr(m, "tags", []) or []
                    if tag not in tags:
                        continue
                    if agent_id:
                        own = f"agent:{agent_id}" in tags
                        shared = include_shared and ("shared" in tags)
                        if not (own or shared):
                            continue
                    out.append({
                        "id": getattr(m, "id", ""), "content": getattr(m, "content", ""),
                        "tags": tags,
                        "_ts": getattr(m, "created_at", None) or getattr(m, "timestamp", None) or "",
                    })
            out.sort(key=lambda x: str(x["_ts"]), reverse=True)
            deduped, seen_c = [], set()
            for x in out:
                if x["content"] in seen_c:
                    continue
                seen_c.add(x["content"])
                deduped.append(x)
            return deduped[:limit]
        except Exception:
            return []

    def delete_memory(self, memory_id):
        """删一条记忆(memory 文件 + 向量)——用于误存偏好的清理(F3)。返回是否删到 memory 文件。
        向量删失败不影响召回正确性:recall 从 list_memories 迭代,文件删了就不会再命中;向量残留
        仅占空间,一并清掉更干净。"""
        fs = getattr(self._hmr, "memory_fs", None)
        vs = getattr(self._hmr, "vector_store", None)
        ok = False
        with self._lock:
            if fs is not None and hasattr(fs, "delete_memory"):
                try:
                    ok = bool(fs.delete_memory(memory_id))
                except Exception:
                    ok = False
            if vs is not None and hasattr(vs, "delete"):
                try:
                    vs.delete(memory_id)
                except Exception:
                    pass
        return ok

    def _filtered_recall(self, query, agent_id, top_k, include_shared):
        """根治路径:在"本 Agent + shared"全集内语义排序。结构不可用则返回 None(触发回退)。"""
        vs = getattr(self._hmr, "vector_store", None)
        fs = getattr(self._hmr, "memory_fs", None)
        if vs is None or fs is None or not hasattr(vs, "vectors") \
                or not hasattr(vs, "embedder") or not hasattr(fs, "list_memories"):
            return None
        try:
            with self._lock:
                mems = fs.list_memories()
                cand = []
                for m in mems:
                    tags = getattr(m, "tags", []) or []
                    if agent_id:
                        own = f"agent:{agent_id}" in tags
                        shared = include_shared and ("shared" in tags)
                        if not (own or shared):
                            continue
                    cand.append((m, tags))
                if not cand:
                    return []
                qv = vs.embedder.embed(query or "")
                scored = []
                for m, tags in cand:
                    vec = vs.vectors.get(getattr(m, "id", None))
                    if vec is None:
                        try:
                            text = getattr(m, "semantic_summary", "") or (m.content or "")[:500]
                            vec = vs.embedder.embed(text)
                        except Exception:
                            continue
                    scored.append((vs._cosine_similarity(qv, vec), m, tags))
                scored.sort(key=lambda x: x[0], reverse=True)
                out = []
                for score, m, tags in scored[:top_k]:
                    out.append({
                        "id": getattr(m, "id", ""),
                        "title": getattr(m, "title", ""),
                        "content": getattr(m, "content", ""),
                        "type": getattr(m, "type", "concept"),
                        "confidence": getattr(m, "confidence", 1.0),
                        "tags": tags,
                        "shared": "shared" in tags,
                        "score": round(float(score), 4),
                    })
                return out
        except Exception:
            return None

    def _legacy_recall(self, query, agent_id, top_k, include_shared):
        """回退路径:全局召回再按 tag 过滤(HMR 结构不可访问时用)。有完整性局限,见 recall。"""
        if agent_id:
            fetch_k = min(max(top_k * 10, 50), 200)
        else:
            fetch_k = top_k
        with self._lock:
            result = self._hmr.recall(query=query, top_k=fetch_k)
        objs = getattr(result, "memory_objects", []) or []
        out = []
        for m in objs:
            tags = getattr(m, "tags", []) or []
            if agent_id:
                own = f"agent:{agent_id}" in tags
                shared = include_shared and ("shared" in tags)
                if not (own or shared):
                    continue
            out.append({
                "id": getattr(m, "id", ""),
                "title": getattr(m, "title", ""),
                "content": getattr(m, "content", ""),
                "type": getattr(m, "type", "concept"),
                "confidence": getattr(m, "confidence", 1.0),
                "tags": tags,
                "shared": "shared" in tags,
            })
            if len(out) >= top_k:
                break
        return out

    def get_workspace(self, agent_id: str):
        with self._lock:
            return self._hmr.get_workspace(agent_id)

    def save_workspace(self, agent_id: str):
        with self._lock:
            return self._hmr.save_workspace(agent_id)

    def save_runtime_state(self, **kwargs):
        with self._lock:
            return self._hmr.save_runtime_state(**kwargs)

    def restore_runtime_state(self):
        with self._lock:
            return self._hmr.restore_runtime_state()

    def get_system_status(self):
        try:
            with self._lock:
                return self._hmr.get_system_status()
        except Exception:
            return {"backend": "HMR", "version": self.version}

    def recent(self, limit: int = 20) -> list:
        """最近的记忆条目(按创建时间倒序),供前端右栏"记忆信息条"展示。
        只回展示所需的轻字段,不回完整 embedding(那是几百维浮点,白白撑大负载)。"""
        out = []
        try:
            with self._lock:
                mfs = getattr(self._hmr, "memory_fs", None)
                mems = mfs.list_memories() if (mfs is not None and hasattr(mfs, "list_memories")) else []
        except Exception:
            mems = []

        def _ts(m):
            v = getattr(m, "created_at", None)
            if v is None:
                return 0.0
            try:
                return v.timestamp() if hasattr(v, "timestamp") else float(v)
            except Exception:
                return 0.0

        try:
            mems = sorted(mems or [], key=_ts, reverse=True)
        except Exception:
            mems = list(mems or [])
        for m in mems[:max(1, int(limit))]:
            tags = list(getattr(m, "tags", None) or [])
            agent = ""
            for t in tags:
                if isinstance(t, str) and t.startswith("agent:"):
                    agent = t.split(":", 1)[1]
                    break
            created = getattr(m, "created_at", None)
            title = (getattr(m, "title", "") or "").strip()
            if not title or self._AUTO_TITLE.match(title):
                title = self._derive_title(
                    getattr(m, "content", "") or getattr(m, "semantic_summary", "")) or title
            created_at = str(created or "")
            if created is not None and hasattr(created, "isoformat"):
                created_at = created.isoformat()
            out.append({
                "id": getattr(m, "id", ""),
                "type": getattr(m, "type", ""),
                "title": title[:120],
                "summary": (getattr(m, "semantic_summary", "") or getattr(m, "content", "") or "")[:180],
                "agent": agent,
                "shared": "shared" in tags,
                "created_at": created_at,
            })
        return out

    def stats(self) -> dict:
        """记忆统计:供前端记忆中心(vecRecall)展示真实数字。"""
        total = 0
        relations = 0
        shared_count = 0
        by_agent: dict[str, Any] = {}
        embedding = "TF-IDF n-gram"
        try:
          with self._lock:
            mems: list[Any] = []
            mfs = getattr(self._hmr, "memory_fs", None)
            if mfs is not None and hasattr(mfs, "list_memories"):
                try:
                    mems = mfs.list_memories() or []
                except Exception:
                    mems = []
            total = len(mems)
            if not total:
                vs0 = getattr(self._hmr, "vector_store", None)
                vecs = getattr(vs0, "vectors", None) if vs0 is not None else None
                if isinstance(vecs, dict):
                    total = len(vecs)
            for m in mems:
                tags = getattr(m, "tags", None)
                if tags is None and isinstance(m, dict):
                    tags = m.get("tags")
                tags = tags or []
                if "shared" in tags:
                    shared_count += 1
                for t in tags:
                    if isinstance(t, str) and t.startswith("agent:"):
                        aid = t.split(":", 1)[1] or "unknown"
                        by_agent[aid] = by_agent.get(aid, 0) + 1
            graph = getattr(self._hmr, "memory_graph", None)
            if graph is not None:
                edges = getattr(graph, "edges", None)
                if edges is not None:
                    relations = len(edges) if hasattr(edges, "__len__") else 0
            emb = getattr(self._hmr, "embedding", None) or getattr(self._hmr, "embedder", None)
            if emb is not None:
                name = type(emb).__name__
                if "openai" in name.lower() or "OpenAI" in name:
                    embedding = "OpenAI Embedding"
                elif "sentence" in name.lower() or "ST" in name:
                    embedding = "Sentence-Transformers"
        except Exception:
            pass
        return {
            "backend": getattr(self, "backend", "HMR"),
            "version": self.version,
            "total_memories": int(total),
            "relations": int(relations),
            "shared": int(shared_count),
            "private": int(max(total - shared_count, 0)),
            "by_agent": by_agent,
            "embedding": embedding,
        }


class MockHMR:
    """HMR 不可用时的最小替代,保证系统可启动。"""

    backend = "Mock"
    version = "mock"

    def get_workspace(self, agent_id: str):
        return type("Workspace", (), {
            "agent_id": agent_id, "active_goal": "", "status": "idle",
            "push_task": lambda self, t: None,
        })()

    def save_workspace(self, agent_id: str):
        pass

    def ingest(self, content, type="concept", agent_id=None, shared=False,
               consent=False, **kwargs):
        if shared:
            guard_shared_write(self, agent_id, content, type, kwargs.get("tags") or [])
        if not consent:
            blocked = guard_sensitive_write(self, agent_id, content, type)
            if blocked is not None and not blocked.allowed:
                return SimpleNamespace(id="", type=type, pending=True,
                                       pending_id=blocked.pending_id, reason=blocked.reason,
                                       categories=list(blocked.categories))
        return SimpleNamespace(id="mock_mem", type=type)

    def recall(self, query: str = "", agent_id: str | None = None, top_k: int = 5, **kwargs):
        from types import SimpleNamespace
        return SimpleNamespace(memory_objects=[])

    def save_runtime_state(self, **kwargs):
        pass

    def restore_runtime_state(self):
        return None

    def get_system_status(self):
        return {"backend": "Mock", "version": "mock"}

    def stats(self) -> dict:
        return {"backend": "Mock", "version": "mock",
                "total_memories": 0, "relations": 0}


def _vecrecall_dir(storage_path: str) -> str:
    """与 HMR 的存储目录强制分开。

    两套后端落盘格式完全不同(HMR 是 memories/*.json + vectors.json,vecRecall 是
    sqlite)。放同一个目录会让新后端初始化时看到"一半旧一半新",切回旧后端时又可能
    被新文件干扰 —— 隔离目录是切换可逆的前提。
    """
    p = Path(str(storage_path))
    if p.name.lower() == "hmr":
        return str(p.parent / "vecrecall")
    return str(p.parent / ((p.name or "memory") + "_vecrecall"))


def _env_first(*names: str, default: str = "") -> str:
    """按顺序取第一个非空环境变量。

    Foxclaw 的配置从 hestiaOS 继承而来,老 .env 里是 `HESTIA_*` 前缀。两个名字都认,
    `FOXCLAW_*` 优先 —— 这样旧配置直接搬过来就能用,不必逐条改名。
    """
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return default


def _memory_chain_enabled() -> bool:
    """哈希链开关:`1/true/yes/on` 为开。只对 vecRecall 生效。"""
    raw = _env_first("FOXCLAW_MEMORY_CHAIN", "HESTIA_MEMORY_CHAIN")
    return raw.lower() in ("1", "true", "yes", "on")


def create_memory_backend(storage_path: str = "./hmr_data", llm_api_key: str | None = None):
    """工厂:默认 HMR;`FOXCLAW_MEMORY_BACKEND=vecrecall` 时才切 vecRecall。

    顺序 vecRecall → HMR → MockHMR,任一层失败退到下一层,且**明确打印**实际用的是
    哪个内核 —— 换记忆后端这种事,日志里必须看得见,不能静默替换。
    """
    choice = _env_first("FOXCLAW_MEMORY_BACKEND", "HESTIA_MEMORY_BACKEND",
                        default="hmr").lower()
    if choice in ("vecrecall", "vec_recall"):
        try:
            from .vec_recall_adapter import VecRecallAdapter
            use_chain = _memory_chain_enabled()
            adapter = VecRecallAdapter(storage_path=_vecrecall_dir(storage_path),
                                       use_chain=use_chain)
            print(f"[Foxclaw] ✓ vecRecall v{adapter.version} 已接入 "
                  f"(backend={adapter.backend}, chain={'on' if use_chain else 'off'}, "
                  f"path={adapter.storage_path})")
            return adapter
        except Exception as e:
            print(f"[Foxclaw] ⚠ vecRecall 不可用 ({e.__class__.__name__}: {e}),回退 HMR")
    elif choice != "hmr":
        print(f"[Foxclaw] ⚠ FOXCLAW_MEMORY_BACKEND={choice!r} 无法识别,按 HMR 处理")

    try:
        hmr = HMRAdapter(storage_path=storage_path, llm_api_key=llm_api_key)
        print(f"[Foxclaw] ✓ HMR v{hmr.version} 已接入 (backend={hmr.backend})")
        return hmr
    except Exception as e:
        print(f"[Foxclaw] ⚠ HMR 不可用 ({e.__class__.__name__}: {e}),降级 MockHMR")
        return MockHMR()
