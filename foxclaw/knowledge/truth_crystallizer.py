"""
TruthCrystallizer —— 把执行真值经 KDE→KEE 结晶进 HKC(v2 ④ Step1)。

审计边界(P2 已厘清):HMR = 情景记忆("发生过什么");HKC = KEE 裁决的知识("什么是真的")。
真值本质是"知识",应经 **KEE 裁决**(去重/合并/冲突,守 Principle 07)进 HKC,而非只塞 HMR。

Step1(叠加、默认关、行为不变):
  · 订阅 `truth.appended`;启用且 HKC 可用时,把真值 fact 作为知识候选**后台**推给
    `hkc.client.ingest_text`(KDE 抽取 → 发 knowledge.created → KEE 去重/合并入图)。
  · **后台线程执行**:KDE 调 LLM,绝不能阻塞 run_task 的同步真值链(truth.appended 处理器
    只提交任务、立即返回)。
  · **双写**:TruthPipeline 的 HMR 共享真值写保留不变;本结晶器只**叠加** HKC 路径。
  · 默认关(FOXCLAW_TRUTH_TO_HKC=1 才开):KDE 有 LLM 成本,先 opt-in;后续步骤再翻默认。
"""
from __future__ import annotations
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, cast, Optional


class TruthCrystallizer:
    def __init__(self, hkc_provider: Callable[[], object], enabled: bool = False):
        self._hkc_provider = hkc_provider     # 惰性取 HKCService(或 None)
        self.enabled = enabled
        self._executor: Optional[ThreadPoolExecutor] = None
        self._closed = False
        self._lock = threading.Lock()
        self._pending: list = []              # 供测试 drain

    def _ex(self) -> Optional[ThreadPoolExecutor]:
        with self._lock:
            if self._closed:
                return None
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="truth-crystallize")
            return self._executor

    def on_truth(self, msg) -> None:
        """订阅 truth.appended 的处理器(同步、非阻塞):只提交后台结晶任务。"""
        if not self.enabled:
            return
        p = getattr(msg, "payload", None) or {}
        if p.get("blocked"):                  # 被拦截任务不结晶(与 TruthPipeline 一致)
            return
        fact = ((p.get("truth") or {}).get("fact") or "").strip()
        if not fact:
            return
        domain = p.get("agent_id") or "execution"
        ex = self._ex()
        if ex is None:
            return
        fut = ex.submit(self._crystallize, fact, domain)
        with self._lock:
            # 【深审修复·长期运行泄漏】_pending 原先只增不减(drain 仅在 close/测试时调用),
            # 启用后每来一次 truth.appended 就永久累积一个已完成 Future → 无界内存泄漏。
            # 改为每次提交时 prune 掉已完成的,_pending 只保留在飞行中的任务(仍支持测试 drain)。
            self._pending = [f for f in self._pending if not f.done()]
            self._pending.append(fut)

    def _crystallize(self, fact: str, domain: str) -> bool:
        """后台执行:惰性取 HKC → ingest_text(KDE→KEE)。任何失败都吞掉,不影响主流程。"""
        try:
            hkc = cast(Any, self._hkc_provider())
        except Exception:
            hkc = None
        if hkc is None or not getattr(hkc, "available", False):
            return False
        try:
            hkc.client.ingest_text(fact, source_title="真值", domain=domain)
            return True
        except Exception:
            return False

    def drain(self, timeout: float = 10.0) -> None:
        """等待所有已提交的结晶任务完成(测试/优雅关停用)。"""
        with self._lock:
            pending = list(self._pending)
            self._pending = []
        for fut in pending:
            try:
                fut.result(timeout=timeout)
            except Exception:
                pass

    def close(self) -> None:
        self.drain()
        with self._lock:
            self._closed = True
            ex = self._executor
        if ex is not None:
            ex.shutdown(wait=True)
