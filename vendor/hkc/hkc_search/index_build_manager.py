"""
hkc-search / index_build_manager.py
────────────────────────────────────
阶段 C 提交:把冷构建从 assemble() 移到后台线程。

为何需要这个模块
~~~~~~~~~~~~~~~~
1.6 万条 KU 用本地 bge-m3 模型全量 embedding 在 CPU 上要 30-60 分钟。
放在 assemble() 同步链里意味着:任何容器初始化都会让服务看起来「卡死」,
Lifespan 启动 60 秒超时必崩。阶段 A(manifest 持久化)+ 阶段 B(事件同步
C/U/D)已经为「后台构建」铺好路:

  · 阶段 A:VectorIndex.load() 安全落盘 + 8 步校验,失配即 _reset()
  · 阶段 A:HybridSearch 暴露 append_bulk / update_ku / remove_ku
  · 阶段 A:同一 vector 文本指纹(text_hashes)可在后台检测哪些 KU 还要重编
  · 阶段 B:任何新 KU 由事件层追加进 vector,manager 跟它不冲突

本模块提供:
  - 单例后台线程(daemon=True)跑增量构建
  - checkpoint 持久化到 <data_dir>/index_build_state.json(原子 os.replace)
  - 与已有 vector.append_bulk 共享 hybrid._lock(查询侧锁)
  - 共享 EmbeddingBackend process 级缓存(绝不二次 load 模型)
  - stats() 返 dict,前端 /stats 拿「正在编码 N/M」做兜底提示

状态机
~~~~~~
    idle ──start()──> building ──完成──> ready ──start()──> building(同 task 跳过)
                          │                                    └─新 task 起新 run)
                          ├──失败──> failed   (可手动 start() 重跑)
                          └──取消──> cancelled (start() 不会自动续,需调用方决定)

设计约束
~~~~~~~~
  · 绝不阻塞 assemble():start() 立即返,线程在后台跑。
  · 绝不在容器 close 路径抛:cancel()/join() 包 try/except,失败静默。
  · 绝不 load 新模型:走 hybrid.vector._backend.encode(),process 级缓存
    (embedding_backends._ST_MODELS)自动复用。
  · 绝不破坏已有 vector 数据:append_bulk 内部走 faiss.add,append 到末尾;manager
    只在「vector 为空」时从头构建,已有数据时跳过。
"""
from __future__ import annotations
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from .embedding_backends import get_st_model

logger = logging.getLogger(__name__)


_BUILD_STATES = ("idle", "building", "ready", "failed", "cancelled")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class IndexBuildManager:
    """冷构建后台化 + checkpoint + 状态观测。

    用法:
        mgr = IndexBuildManager(data_dir, hybrid_search, graph_store, ...)
        mgr.start()             # 立即返,后台线程跑构建
        snap = mgr.stats()      # 任意时刻取状态
        mgr.cancel()            # 设取消标志(线程在 batch 边界响应)
        mgr.join(timeout=2.0)   # 限时等线程退出(用于 close 路径)
    """

    STATE_FILE = "index_build_state.json"

    def __init__(
        self,
        data_dir: str,
        hybrid_search,
        graph_store,
        batch_size: int = 64,
        max_failures: int = 100,
        checkpoint_every_batches: int = 5,
        on_ready: Callable[[], None] | None = None,   # B3:ready 时回调(flush pending)
    ):
        """data_dir:           索引/状态落盘目录
        hybrid_search:      HybridSearch 实例(vector + bm25)
        graph_store:        SQLiteGraphStore 实例,query_all(limit) 拉活动 KU
        batch_size:         每次 encode 的 KU 数;bge-m3 64 条/batch 在 CPU 上
                            约 3-5 秒一批,平衡内存与延迟
        max_failures:       单任务内连续失败超过该数 → 任务标 failed
        checkpoint_every_batches: 每 N 个 batch 写一次 state.json(防进程崩丢进度)
        on_ready:           可选回调,任务标 ready 时在 _mark_ready_locked 内调
                            (B3:container 借此 flush pending_ops 队列,避免循环引用)
        """
        self.data_dir = data_dir
        self.hybrid = hybrid_search
        self.graph_store = graph_store
        self.batch_size = batch_size
        self.max_failures = max_failures
        self.checkpoint_every_batches = max(1, checkpoint_every_batches)
        self.on_ready = on_ready

        # 状态
        self.task_id:      str = ""
        self.index_version: str = ""
        self.state:        str = "idle"
        self.source_total: int = 0
        self.scanned:      int = 0
        self.ready:        int = 0
        self.failed:       int = 0
        self.failures:     list[dict] = []
        self.error_summary: str = ""
        self.started_at:   str | None = None
        self.finished_at:  str | None = None
        self.last_checkpoint_at: str | None = None

        # 线程与同步
        self._lock   = threading.Lock()      # 保护上述 11 个字段
        self._cancel = threading.Event()     # 取消标志
        self._thread: threading.Thread | None = None

        # 持久化路径
        self.state_path = os.path.join(data_dir, self.STATE_FILE)
        self._load_state_from_disk()

    # ── 持久化 ──────────────────────────────────────────────

    def _load_state_from_disk(self) -> None:
        """启动时读上次 state.json(若有)。**不**自动续跑 —— 是否续跑由
        container.assemble() 决策(根据 vector 是否已加载 + 库 KU 数判断)。
        这里只恢复观测字段:让 stats() 在 idle 状态下也能展示「上次构建结果」。"""
        if not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            logger.warning("index_build_state.json 读取失败(忽略): %s", e)
            return
        for k in ("task_id", "index_version", "state", "source_total",
                  "scanned", "ready", "failed", "failures", "error_summary",
                  "started_at", "finished_at", "last_checkpoint_at"):
            if k in d:
                setattr(self, k, d[k])

    def _save_state_locked(self) -> None:
        """在 self._lock 内调。原子写:tmp + os.replace 防写入中崩溃污染。"""
        d = {
            "task_id":            self.task_id,
            "index_version":      self.index_version,
            "state":              self.state,
            "source_total":       self.source_total,
            "scanned":            self.scanned,
            "ready":              self.ready,
            "failed":             self.failed,
            "failures":           self.failures[-20:],   # 限长,只保留最近 20 个
            "error_summary":      self.error_summary,
            "started_at":         self.started_at,
            "finished_at":        self.finished_at,
            "last_checkpoint_at": self.last_checkpoint_at,
        }
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.state_path)
        except Exception as e:
            logger.warning("index_build_state.json 落盘失败(非致命): %s", e)

    # ── 对外 API ──────────────────────────────────────────────

    def start(self) -> str | None:
        """提交后台构建任务。
        - 已有线程在跑 → 返现 task_id(幂等,不重起)。
        - 状态在 building → 返现 task_id(不打断)。
        - vector 已加载且 >= 库 KU 数 → 返 None(无需构建,标 ready)。
        - 否则起新 daemon 线程 → 返新 task_id。

        返回 None 意味着「无需构建」,这是 _load_persisted_index_and_bm25()
        一次性加载完磁盘索引后最常见的情况。
        """
        with self._lock:
            # 已在跑就幂等
            if self._thread is not None and self._thread.is_alive():
                return self.task_id or None
            # 决定要不要构建
            try:
                kus = self.graph_store.query_all(limit=99999999) or []
            except Exception as e:
                logger.warning("manager.start() 扫描库失败: %s", e)
                kus = []
            vec_size = self.hybrid.vector.size() if self.hybrid.vector else 0
            if vec_size > 0 and vec_size >= len(kus):
                # 磁盘已覆盖,无需构建。state 标 ready
                self.state       = "ready"
                self.source_total= len(kus)
                self.ready       = vec_size
                self.finished_at = _now_iso()
                self._save_state_locked()
                logger.info("IndexBuildManager 跳过构建(vector=%d >= source=%d)",
                            vec_size, len(kus))
                return None
            # 续跑检查:磁盘有部分 vector(state 是上次未完成的)
            # → 仍起新 task_id 走完整重扫(避免依赖不稳定的 mid-build state)
            self.task_id       = "build-" + uuid.uuid4().hex[:8]
            self.index_version = "v-" + uuid.uuid4().hex[:6]
            self.state         = "building"
            self.source_total  = len(kus)
            self.scanned       = 0
            self.ready         = 0
            self.failed        = 0
            self.failures      = []
            self.error_summary = ""
            self.started_at    = _now_iso()
            self.finished_at   = None
            self.last_checkpoint_at = None
            self._cancel.clear()
            self._save_state_locked()
            self._thread = threading.Thread(
                target=self._run, name=f"idx-build-{self.task_id}", daemon=True,
            )
            self._thread.start()
            return self.task_id

    def cancel(self) -> None:
        """设取消标志。线程在 batch 边界自检并停。**不**等线程退出,要等
        线程退出请调 join(timeout)。"""
        self._cancel.set()
        with self._lock:
            if self.state == "building":
                self.state = "cancelled"
                self.finished_at = _now_iso()
                self._save_state_locked()

    def join(self, timeout: float | None = None) -> bool:
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "index_state":          self.state,
                "index_version":        self.index_version,
                "task_id":              self.task_id,
                "source_total":         self.source_total,
                "scanned":              self.scanned,
                "ready":                self.ready,
                "pending":              max(0, self.source_total - self.scanned),
                "failed":               self.failed,
                "failures_preview":     self.failures[:5],
                "started_at":           self.started_at,
                "finished_at":          self.finished_at,
                "last_checkpoint_at":   self.last_checkpoint_at,
                "error_summary":        self.error_summary,
                "vector_size":          self.hybrid.vector.size() if self.hybrid.vector else 0,
            }

    # ── 后台线程主循环 ─────────────────────────────────────

    def _run(self) -> None:
        """单任务执行:
        1) 扫描全量 active KU
        2) 算哪些「不在 vector」(按 ku_id)
        3) 批量 encode + append_bulk(走 shared backend cache,绝不 load 新模型)
        4) 每 N batch 写一次 checkpoint
        5) 取消 / 失败阈值到 → 收尾
        """
        logger.info("IndexBuildManager[%s] 启动: scan %d 条 KU",
                    self.task_id, self.source_total)
        try:
            kus = self.graph_store.query_all(limit=99999999) or []
            # 仅处理 active 状态(不索引已删/废弃)
            kus = [k for k in kus if getattr(k, "status", "active") == "active"]
            # 排除 vector 中已有的(纯增量)
            existing = set(self.hybrid.vector._ku_ids or [])
            to_build = [k for k in kus if k.ku_id not in existing]
            with self._lock:
                self.source_total = len(kus)
                self.scanned      = len(kus) - len(to_build)
            if not to_build:
                logger.info("IndexBuildManager[%s] 库已全在 vector,空转结束",
                            self.task_id)
                self._mark_ready_locked()
                return
            # 批量循环
            batch_no = 0
            consecutive_failures = 0
            for i in range(0, len(to_build), self.batch_size):
                if self._cancel.is_set():
                    logger.info("IndexBuildManager[%s] 收到取消信号,batch=%d 处停",
                                self.task_id, batch_no)
                    return
                batch = to_build[i:i + self.batch_size]
                try:
                    # append_bulk 内部走 backend.encode() → 走 _ST_MODELS 共享缓存
                    # (embedding_backends.get_st_model 单一入口,绝不 load 重复模型)
                    # B1+B2:用 _write_lock(写-写互斥),读(search)不阻塞;
                    # vector.append_bulk 内部 encode 锁外,仅 faiss.add 锁内(微秒)
                    with self.hybrid._write_lock:
                        self.hybrid.vector.append_bulk(batch)
                    with self._lock:
                        self.scanned += len(batch)
                        self.ready   += len(batch)
                    consecutive_failures = 0
                except Exception as e:
                    consecutive_failures += 1
                    with self._lock:
                        self.failed += len(batch)
                        self.failures.append({
                            "batch_start": i,
                            "count":       len(batch),
                            "first_ku":    getattr(batch[0], "ku_id", "?"),
                            "error":       str(e)[:500],
                        })
                        if self.failed > self.max_failures:
                            self.state         = "failed"
                            self.error_summary = (
                                f"失败累计 {self.failed} 条 > 阈值 {self.max_failures};"
                                f" 最近错误: {e}"
                            )
                            self.finished_at = _now_iso()
                            self._save_state_locked()
                            return
                    if consecutive_failures >= 3:
                        # 连续 3 batch 全失败 → 网络/模型层问题,停
                        with self._lock:
                            self.state         = "failed"
                            self.error_summary = (
                                f"连续 {consecutive_failures} 批失败: {e}"
                            )
                            self.finished_at   = _now_iso()
                            self._save_state_locked()
                        return
                batch_no += 1
                if batch_no % self.checkpoint_every_batches == 0:
                    with self._lock:
                        self.last_checkpoint_at = _now_iso()
                        self._save_state_locked()
                    logger.info("IndexBuildManager[%s] checkpoint batch=%d "
                                "scanned=%d/%d ready=%d failed=%d",
                                self.task_id, batch_no,
                                self.scanned, self.source_total,
                                self.ready, self.failed)
            self._mark_ready_locked()
            logger.info("IndexBuildManager[%s] 完成: ready=%d failed=%d",
                        self.task_id, self.ready, self.failed)
        except Exception as e:
            logger.exception("IndexBuildManager[%s] 顶层异常: %s", self.task_id, e)
            with self._lock:
                self.state         = "failed"
                self.error_summary = f"顶层异常: {e}"
                self.finished_at   = _now_iso()
                self._save_state_locked()

    def _mark_ready_locked(self) -> None:
        """vector 索引已就绪 → 落盘 + 标 ready。**在 _lock 内调**。
        B3:state 变 ready 后,释放 _lock 再调 on_ready 回调(container 借此
        flush pending_ops 队列)—— 避免在 _lock 内回调又间接持 _lock 死锁。"""
        with self._lock:
            self.state              = "ready"
            self.finished_at        = _now_iso()
            self.last_checkpoint_at = self.finished_at
            self._save_state_locked()
            # 落盘 vector(snapshot)
            try:
                vec_path = os.path.join(self.data_dir, "search_vector")
                self.hybrid.vector.save(vec_path)
            except Exception as e:
                logger.warning("manager 落盘 vector 失败(非致命,下次启动会重扫): %s", e)
        # 释放 _lock 后再调 on_ready(防回调里 acquire 任何 manager 内的锁嵌套)
        if self.on_ready is not None:
            try:
                self.on_ready()
            except Exception as e:
                logger.warning("on_ready 回调失败(非致命,pending 可能延后 flush): %s", e)
