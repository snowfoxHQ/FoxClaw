"""
hkc-api / container.py
应用容器：按依赖顺序装配 HKC 所有组件。

装配顺序（依赖从下到上）：
  1. IDGenerator       ← 无依赖
  2. GraphStore        ← 无依赖
  3. EventBus          ← 无依赖
  4. KEE               ← GraphStore + EventBus + IDGen（订阅 knowledge.created）
  5. ACE               ← GraphStore + EventBus + IDGen
  6. KDE               ← GraphStore + EventBus + IDGen
  7. HybridSearch      ← GraphStore（+ 可选向量后端）

容器是单例，整个 API 进程共享一套组件。
"""
# TODO: Replace with proper package install before v1 release

import logging
import threading
from collections import deque
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class HKCContainer:
    """HKC 组件容器。一次装配，全程共享。"""

    def __init__(
        self,
        data_dir:        str = "./hkc_data",
        embedding_kind:  str = "stub",   # stub | local | tei
        embedding_url:   str = "http://localhost:8080",
        embedding_model: Optional[str] = None,  # local/auto 后端的模型名(如 BAAI/bge-m3)
        llm_api_key:     Optional[str] = None,
        llm_model:       Optional[str] = None,
        llm_provider:    str = "anthropic",
        llm_base_url:    Optional[str] = None,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._embedding_kind = embedding_kind
        self._embedding_url  = embedding_url
        self._embedding_model = embedding_model
        self._llm_api_key    = llm_api_key
        self._llm_model      = llm_model
        self._llm_provider   = llm_provider
        self._llm_base_url   = llm_base_url

        # 组件占位
        self.id_gen      = None
        self.graph_store = None
        self.event_bus   = None
        self.kee         = None
        self.ace         = None
        self.kde         = None
        self.search      = None
        self.ingest_jobs = None      # 异步摄入任务注册表(方案A)
        self.synthesis   = None      # 综合页 LLM 综述(派生只读视图,不进知识库)
        self.crystallizer = None     # Crystallizer 摄入适配器(集成层入口:外部推知识候选)
        self.index_build_manager = None  # 阶段 C:后台冷构建管理器(无磁盘索引时跑)

        # B3:IndexBuildManager 跑时(building)_sync_ku 入队,跑完(ready)flush。
        # maxlen 防 OOM(超出从头丢,降级而非阻断,摄入仍走同步路径)
        self._pending_ops: deque = deque(maxlen=10000)
        self._pending_lock = threading.Lock()

        self._assembled = False

    def assemble(self) -> "HKCContainer":
        """按依赖顺序装配所有组件。幂等。"""
        if self._assembled:
            return self

        from hkc_core.utils.id_gen import IDGenerator
        from hkc_core.graph.sqlite_store import SQLiteGraphStore
        from hkc_kep.event_bus import EventBus
        from hkc_kee.kee import KnowledgeEvolutionEngine
        from hkc_ace.ace import AbilityCompilerEngine
        from hkc_kde.kde import KnowledgeDigestEngine
        from hkc_search.hybrid import HybridSearch
        from hkc_search.vector_search import VectorIndex
        from hkc_search.embedding_backends import make_backend

        # 1. ID 生成器
        self.id_gen = IDGenerator(str(self.data_dir / "id_counters.db"))

        # 2. GraphStore
        self.graph_store = SQLiteGraphStore(str(self.data_dir / "hkc.db"))

        # 3. EventBus
        self.event_bus = EventBus(str(self.data_dir / "events.db"))

        # 4. KEE（构造时自动订阅 knowledge.created）
        self.kee = KnowledgeEvolutionEngine(
            self.graph_store, self.event_bus, self.id_gen
        )

        # 5. ACE
        self.ace = AbilityCompilerEngine(
            self.graph_store, self.event_bus, self.id_gen,
            output_dir=str(self.data_dir / "abilities"),
        )

        # 6. KDE
        self.kde = KnowledgeDigestEngine(
            self.graph_store, self.event_bus, self.id_gen,
            api_key=self._llm_api_key, model=self._llm_model,
            provider=self._llm_provider, base_url=self._llm_base_url,
        )

        # 7. HybridSearch（向量后端可配置）
        backend_kwargs = {}
        if self._embedding_kind == "tei":
            backend_kwargs["base_url"] = self._embedding_url
        # local/auto 后端可指定模型名(如 BAAI/bge-m3,1024 维,维度由模型自动校正);
        # 不传则用 LocalSTBackend 默认模型,行为与此前一致。tei 不接受 model_name,不传。
        if self._embedding_model and self._embedding_kind in ("local", "auto"):
            backend_kwargs["model_name"] = self._embedding_model
        backend = make_backend(self._embedding_kind, **backend_kwargs)
        vector_index = VectorIndex(backend=backend)
        self.search  = HybridSearch(self.graph_store, vector_index=vector_index)

        # 启动时加载持久化索引 + 准备 BM25。阶段 C 后,**全量 build 由 IndexBuildManager
        # 后台线程承担**,assemble() 内部只做「加载已存在」+「BM25 重建」,保证 Lifespan
        # 启动不被 30-60 分钟的冷编码卡住。IndexBuildManager 见下段。
        self._load_persisted_index_and_bm25()

        # 阶段 C:起后台冷构建 manager。它会自动:
        #   · vector 已覆盖库 → 立即返,标 ready
        #   · vector 部分覆盖 / 为空 → 启 daemon 线程,扫描 + encode + append_bulk
        # 全部完成后,manager 内部 save() 落盘,下次启动 assemble() 直接加载。
        from hkc_search.index_build_manager import IndexBuildManager
        import os as _os
        self.index_build_manager = IndexBuildManager(
            data_dir=str(self.data_dir),
            hybrid_search=self.search,
            graph_store=self.graph_store,
            batch_size=int(_os.environ.get("HKC_BUILD_BATCH_SIZE", "64")),
            max_failures=int(_os.environ.get("HKC_BUILD_MAX_FAILURES", "100")),
            checkpoint_every_batches=int(_os.environ.get("HKC_BUILD_CHECKPOINT_BATCHES", "5")),
            on_ready=lambda: self.flush_pending_ops(),  # B3:ready 时 flush pending 队列
        )
        self.index_build_manager.start()

        # 新知识写入后自动更新搜索索引
        self._wire_search_index_updates()

        # 8. 异步摄入任务注册表(后台线程池执行文件摄入)
        from hkc_api.ingest_jobs import IngestJobRegistry
        self.ingest_jobs = IngestJobRegistry(max_workers=4)

        # 9. 综合页 LLM 综述服务(派生只读视图;缓存独立 synthesis.db,不进知识库,守 Principle 07)
        from hkc_api.synthesis import SynthesisService
        self.synthesis = SynthesisService(
            self.graph_store, self.search, self.kde,
            str(self.data_dir / "synthesis.db"),
        )

        # 10. Crystallizer 摄入适配器(集成层入口):外部系统经 POST /knowledge/crystallize
        #     推知识候选,走 HKCIngress → KDE → KEE。守边界:只产候选,不做知识身份判定。
        from hkc_crystallizer.ingress import HKCIngress
        self.crystallizer = HKCIngress(self.kde)

        self._embedding_name = backend.name   # 实际生效的向量后端名(供 /stats 展示)
        self._assembled = True
        logger.info("HKC 容器装配完成: data_dir=%s embedding=%s",
                    self.data_dir, backend.name)
        return self

    def _load_persisted_index_and_bm25(self):
        """阶段 C:启动期**只**加载磁盘索引 + 重建 BM25,**不**做全量 encode,
        **不**做增量补算 —— 这两件事交给 IndexBuildManager 后台线程。
        - vector.load:从 search_vector.faiss / .meta 加载(阶段 A 8 步校验失败 → _reset)
        - bm25.build:从 store 拉 active KU 重建(快,毫秒级,不会卡启动)
        设计目标:assemble() 整体在秒级返回(在 1.6 万 KU 库上,< 5s),
        不再因为 30-60 分钟的冷编码被 Lifespan 启动超时打断。
        """
        vec_path = str(self.data_dir / "search_vector")
        try:
            kus = self.graph_store.query_all(limit=99999999) or []
        except Exception:
            kus = []
        loaded_n = 0
        if kus:
            try:
                if self.search.vector.load(vec_path):
                    loaded_n = self.search.vector.size()
                    logger.info(
                        "向量索引已从磁盘加载: %d 条 (库内共 %d 条,余下交 manager 后台)",
                        loaded_n, len(kus))
            except Exception as e:
                logger.warning("加载持久化向量索引失败,manager 会兜底: %s", e)
        # BM25 必须重建一次(它不持久化;且重建开销极小,毫秒级)
        if kus:
            try:
                self.search.bm25.build(kus)
            except Exception as e:
                logger.warning("BM25 重建失败(非致命,search 会回退 bm25=None 保护): %s", e)
        if not kus:
            logger.info("库为空,无需构建索引")

    def _wire_search_index_updates(self):
        """订阅 KU 事件,统一走 _sync_ku 五分支。

        阶段 B 修正:
          ① CREATED 处理器必须**查 status** 后再走 _sync_ku(ku_id) —— 重复 KU 已被
             KEE 软删,append_ku 会把同 id 推进 vector,导致检索返回「已删」内容。
          ② UPDATED 处理器也要查 status —— KEE 状态机把 Claim 从 active 改成
             superseded/rejected,vector 必须同步 remove,否则检索召回已废弃 KU。
          ③ DELETED 处理器不查 store(被删了 get=None),直接 vector.remove_ku。

        _sync_ku 五分支:
          1) store.get(ku_id) is None               → vector.remove(兜底)
          2) status == 'deleted'                    → vector.remove
          3) status not in {active}                 → vector.remove (superseded/rejected/disputed)
          4) active + 不在 vector                   → vector.append_ku
          5) active + 在 vector + text_hash 变      → vector.update_ku(remove+append)
             5b) active + 在 vector + text_hash 同   → noop (同 hash 跳过 encode)

        五分支都包在 try/except:单个 KU 同步失败不阻断其它 KU(继续下一条)。
        """
        from hkc_kep.event_bus import KEPEvents

        def _on_ku_event(event_name: str, event: dict):
            ku_ids = event.get("payload", {}).get("ku_ids", [])
            if not ku_ids and "ku_id" in event.get("payload", {}):
                ku_ids = [event["payload"]["ku_id"]]
            for ku_id in ku_ids:
                self._sync_ku(ku_id)

        self.event_bus.subscribe(KEPEvents.KNOWLEDGE_CREATED,
                                 lambda e: _on_ku_event("CREATED", e))
        self.event_bus.subscribe(KEPEvents.KNOWLEDGE_UPDATED,
                                 lambda e: _on_ku_event("UPDATED", e))
        self.event_bus.subscribe(KEPEvents.KNOWLEDGE_DELETED,
                                 lambda e: _on_ku_event("DELETED", e))

    def _sync_ku(self, ku_id: str) -> None:
        """B3 入口:IndexBuildManager building 时入队(微秒返回,不持任何锁);
        其他状态走 _do_sync_ku(原五分支逻辑)。
        单一 KU 失败不抛,只 log warning —— 摄入任务里 50 条 KU 失败 1 条不应拖累其余。

        B3 设计要点(2026-10-16 修正):
          - **不**在 _sync_ku 入口同步 flush(早期版本有 bug:每次入队都
            同步调 flush_pending_ops(),IndexBuildManager 跑时持 hybrid._write_lock
            3-5s/batch(内含 encode),user 上传线程 acquire 同一锁等 3-5s
            → 整个 ingest pipeline 卡死 30-50s)。
          - 依赖 on_ready 回调集中 flush(IndexBuildManager._mark_ready_locked
            触发,state=ready 时 hybrid._write_lock 不再被持,flush 立即完成)。
          - 兜底:on_ready 漏掉的极端情况,user 后续上传走 _do_sync_ku
            同步路径(state 已变 ready,走 else 分支)。
        """
        # B3:building 期间走队列,绕开 _sync_ku 内部的 graph_store.get + hybrid.* 锁
        if (self.index_build_manager is not None
                and self.index_build_manager.state == "building"):
            with self._pending_lock:
                self._pending_ops.append(("sync", ku_id))
            # 不在入口同步 flush —— 由 on_ready 回调集中处理
            return
        self._do_sync_ku(ku_id)

    def _do_sync_ku(self, ku_id: str) -> None:
        """五分支同步单条 KU 到 vector(原 _sync_ku 逻辑,B3 入口和 flush 共用)。
        单一 KU 失败不抛,只 log warning。
        """
        try:
            ku = self.graph_store.get(ku_id)
        except Exception as e:
            logger.warning("_sync_ku 读 store 失败(%s): %s", ku_id, e)
            return
        if ku is None or getattr(ku, "status", "") == "deleted":
            # 1) / 2) — KU 不存在或已删,删 vector
            try:
                self.search.remove_ku(ku_id)
            except Exception as e:
                logger.warning("vector.remove_ku(%s) 失败: %s", ku_id, e)
            return
        if getattr(ku, "status", "active") not in ("active",):
            # 3) — superseded / rejected / disputed(非 active 状态)也不该被检索召回
            try:
                self.search.remove_ku(ku_id)
            except Exception as e:
                logger.warning("vector.remove_ku(%s) 失败: %s", ku_id, e)
            return
        # Claim 还要看 claim_status(可能 active 但 claim_status 是 superseded)
        if getattr(ku, "ku_type", None) and getattr(ku.ku_type, "value", "") == "Claim":
            extra = getattr(ku, "to_dict", lambda: {} )().get("extra", {}) or {}
            cs = extra.get("claim_status", "active")
            if cs in ("superseded", "rejected", "disputed"):
                try:
                    self.search.remove_ku(ku_id)
                except Exception as e:
                    logger.warning("vector.remove_ku(%s) 失败: %s", ku_id, e)
                return
        # 4) / 5) / 5b)
        in_vector = ku_id in (self.search.vector._ku_ids or [])
        if not in_vector:
            try:
                self.search.append_ku(ku)
            except Exception as e:
                logger.warning("vector.append_ku(%s) 失败: %s", ku_id, e)
        else:
            try:
                # update_ku 内部用 text_hash 检测:同 hash 跳过 encode,变才重编
                self.search.update_ku(ku)
            except Exception as e:
                logger.warning("vector.update_ku(%s) 失败: %s", ku_id, e)

    def flush_pending_ops(self) -> int:
        """B3:flush 队列里 _sync_ku 攒下的待处理 op。
        - atomic swap:持 pending_lock 取队列副本 + 清空(几微秒)
        - 释放 lock 后批量执行(slow 操作在锁外,调 _do_sync_ku 走原五分支)
        - 返回 flush 数量(供 on_ready 回调 / 调试)
        设计:IndexBuildManager ready 时调一次;每次 _sync_ku 入队也自检
        flush(避免 ready 事件错过)。风险:deque maxlen=10000 超出从头丢;
        但 _sync_ku 入队时自检 state,ready 后立即 flush,正常不积压。"""
        with self._pending_lock:
            if not self._pending_ops:
                return 0
            pending = list(self._pending_ops)
            self._pending_ops.clear()
        flushed = 0
        for op in pending:
            try:
                op_type, ku_id = op[0], op[1]
                if op_type == "sync":
                    # 调 _do_sync_ku(跳过 B3 检测,强制同步执行,避免再次入队死循环)
                    self._do_sync_ku(ku_id)
                flushed += 1
            except Exception as e:
                logger.warning("flush_pending_ops 失败[%s/%s]: %s",
                               op[0], op[1], e)
        return flushed

    def stats(self) -> dict:
        """返回系统整体统计。"""
        if not self._assembled:
            return {"assembled": False}
        s = self.graph_store.stats()
        s["assembled"]      = True
        s["data_dir"]       = str(self.data_dir.resolve())   # 绝对路径,前端显示 + 排查"连错目录"
        s["embedding"]      = getattr(self, "_embedding_name", "")  # 生效的向量后端(stub 无语义)
        s["bm25_size"]      = self.search.bm25.size()
        s["vector_size"]    = self.search.vector.size()
        # 阶段 C:把后台构建状态也吐出,前端可做「正在编码 N/M」提示
        if self.index_build_manager is not None:
            s["index_build"] = self.index_build_manager.stats()
        else:
            s["index_build"] = {"index_state": "idle"}
        s["abilities"]      = self.ace.list_available()
        # B3 监控:IndexBuildManager building 期间 _sync_ku 入队的 KU 数
        # (= 待 flush 积压)。ready 后被 on_ready 回调一次性清零
        s["pending_ops"]    = len(self._pending_ops)
        return s

    def close(self):
        """关闭资源。每次 reset_container(测试 tearDown / 服务热重载)都会调,
        必须把线程池和额外的 SQLite 连接一并释放,否则会累积线程/文件描述符泄漏。"""
        # 阶段 C:先停后台构建线程(它持有 self.search / self.graph_store 引用),
        # 再释放 SQLite。否则若线程在 close 期间还在跑,SQLite 已被 close 句柄释放,
        # 线程内 append_bulk 会爆「database is closed」。
        if getattr(self, "index_build_manager", None):
            try:
                self.index_build_manager.cancel()
                # 限时 join:不阻塞 close 太久(2s 内退出是合理预期,batch_size=64 bge-m3
                # 约 3-5s,但 cancel 标志会让它在 batch 边界停)。
                self.index_build_manager.join(timeout=2.0)
            except Exception:
                pass
        if self.ingest_jobs:
            try: self.ingest_jobs.shutdown()
            except Exception: pass
        if self.synthesis:
            try: self.synthesis.cache.close()
            except Exception: pass
        # 4 个 SQLite 连接必须全部释放,否则 Windows 下数据目录删不掉(WinError 32)、
        # 长期运行累积 fd 泄漏。此前只关了 graph_store,漏了 event_bus/id_gen(融合时暴露)。
        if getattr(self, "event_bus", None):
            try: self.event_bus.close()
            except Exception: pass
        if getattr(self, "id_gen", None):
            try: self.id_gen.close()
            except Exception: pass
        if self.graph_store:
            self.graph_store.close()


# ── 全局单例 ─────────────────────────────────────────────────

_container: Optional[HKCContainer] = None


def get_container() -> HKCContainer:
    """获取全局容器（FastAPI 依赖注入用）。"""
    global _container
    if _container is None:
        raise RuntimeError("容器未初始化，请先调用 init_container()")
    return _container


def init_container(**kwargs) -> HKCContainer:
    """初始化全局容器。"""
    global _container
    _container = HKCContainer(**kwargs).assemble()
    return _container


def reset_container():
    """重置全局容器（测试用）。"""
    global _container
    if _container:
        _container.close()
    _container = None
