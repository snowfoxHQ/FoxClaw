"""
CapabilityRegistry —— 能力注册中枢(P1-3:God Object → Composition Root)。

审计结论:Foxclaw.__init__ 是个 God Object,子系统之间还形成两处对象环
(Supervisor⇄Orchestrator、agents⇄search_runtime⇄agent_port⇄agents)——组件直接
互相持有具体引用,违反 Clean Architecture。

方案:引入单一注册中枢。组件不再直接持有"同伴组件"的具体引用,而是持有本注册表、
按名字**惰性解析**(依赖倒置:依赖注册表抽象,而非具体同伴)。于是:
  · 直接的"A→B 且 B→A"互持消失(两处直接环被打断);
  · 解析统一走一个 hub(Composition Root 模式),构造顺序不再产生初始化环;
  · 支持 register_lazy:重/可选能力(HKC、SearchRuntime)首次被解析时才构造。

注:hub 与组件之间仍是可达环(注册表持有组件、组件持有注册表),这是 Service Locator
的固有形态,由 GC 正常回收;本次要消除的是"具体同伴之间的直接互持",而非对象图可达性。
"""
from __future__ import annotations
import threading
from typing import Any, Callable


class CapabilityRegistry:
    """按名字注册/解析能力。支持惰性(首次解析才构造)。线程安全。"""

    def __init__(self):
        self._caps: dict[str, Any] = {}
        self._lazy: dict[str, Callable[[], Any]] = {}
        self._lock = threading.RLock()             # 只护 _caps/_lazy 表(快照,持锁时间极短)
        self._build_locks: dict[str, threading.Lock] = {}   # name -> 构造锁(per-name)

    def register(self, name: str, handle: Any) -> None:
        """注册一个已构造好的能力实例(覆盖同名)。"""
        with self._lock:
            self._caps[name] = handle

    def register_lazy(self, name: str, factory: Callable[[], Any]) -> None:
        """注册惰性能力:首次 get(name) 时调用 factory 构造并缓存。"""
        with self._lock:
            self._lazy[name] = factory

    def get(self, name: str, default: Any = None) -> Any:
        """解析能力。已注册直接返回;惰性则首次构造并缓存;都没有返回 default。

        【F1 修复】慢惰性构造(如 search_runtime 首建要装 HKC+载模型)**不持全局锁**,
        改用 per-name 构造锁:同一能力串行构造(只建一次、无双重构造),不同能力互不阻塞
        (避免"首建 search 时,并发的 cross_request 解析 agents 被卡住")。factory 内部
        可安全再 get 其它能力(全局锁此时未被本调用持有)。"""
        with self._lock:
            if name in self._caps:
                return self._caps[name]
            factory = self._lazy.get(name)
            if factory is None:
                return default
            blk = self._build_locks.get(name)
            if blk is None:
                blk = self._build_locks[name] = threading.Lock()
        # 出全局锁,用 per-name 锁串行化"同一能力"的构造
        with blk:
            with self._lock:
                if name in self._caps:      # 双检:构造期间可能已被别的线程建好
                    return self._caps[name]
            inst = factory()                # 慢构造:不持全局锁,不阻塞其它能力解析
            with self._lock:
                self._caps[name] = inst
                return inst

    def has(self, name: str) -> bool:
        with self._lock:
            return name in self._caps or name in self._lazy

    def names(self) -> list[str]:
        with self._lock:
            return sorted(set(self._caps) | set(self._lazy))
