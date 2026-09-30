"""
Memory Scheduler - 记忆调度器
HMR v1.5 新增

这是从"RAG系统"到"记忆操作系统"的关键一步。

类比操作系统：
    CPU Scheduler   → Memory Scheduler
    进程调度策略    → 记忆调度策略
    优先级队列      → 热缓存（Hot Cache）
    内存置换        → 记忆压缩/淘汰

Memory Scheduler 决定：
    ├── 用什么策略召回（semantic / temporal / graph / jit）
    ├── 召回多深（top_k 动态计算）
    ├── 什么时候用 JIT 多步 vs 单次检索
    ├── 哪些记忆常驻热缓存（避免重复检索）
    └── 压缩/剪枝何时触发
"""

import time
import threading
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from ..core.models import MemoryObject, RuntimeState


# ============================================================================
# 召回策略枚举
# ============================================================================

class RecallStrategy(Enum):
    SEMANTIC   = "semantic"    # 纯向量相似度
    TEMPORAL   = "temporal"    # 按时间权重（新鲜度优先）
    GRAPH      = "graph"       # 图路径检索（依赖关系）
    JIT        = "jit"         # 多步推理检索
    HYBRID     = "hybrid"      # 混合策略


# ============================================================================
# 调度计划
# ============================================================================

@dataclass
class RecallPlan:
    """调度器生成的召回计划"""
    strategy: RecallStrategy
    top_k: int
    use_jit: bool
    jit_max_steps: int
    cache_result: bool              # 是否缓存本次结果
    reasoning: str                  # 为什么选这个策略

    def __str__(self):
        return (
            f"策略={self.strategy.value}, top_k={self.top_k}, "
            f"JIT={'是' if self.use_jit else '否'}, "
            f"原因={self.reasoning}"
        )


# ============================================================================
# 热缓存（Hot Cache）
# ============================================================================

class HotCache:
    """
    热缓存：常用记忆保持在内存，避免重复检索。

    策略：LRU（最近最少使用）+ 时间过期
    """

    def __init__(self, max_size: int = 50, ttl_seconds: int = 300):
        self.max_size = max_size
        self.ttl = ttl_seconds
        self._cache: Dict[str, Tuple[List[MemoryObject], float]] = {}  # key → (memories, timestamp)
        self._access_order: List[str] = []  # LRU 队列
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[List[MemoryObject]]:
        with self._lock:
            if key not in self._cache:
                return None
            memories, ts = self._cache[key]
            if time.time() - ts > self.ttl:
                del self._cache[key]
                self._access_order.remove(key)
                return None
            # 更新 LRU
            self._access_order.remove(key)
            self._access_order.append(key)
            return memories

    def set(self, key: str, memories: List[MemoryObject]):
        with self._lock:
            if key in self._cache:
                self._access_order.remove(key)
            elif len(self._cache) >= self.max_size:
                # 淘汰最久未使用的
                oldest = self._access_order.pop(0)
                del self._cache[oldest]

            self._cache[key] = (memories, time.time())
            self._access_order.append(key)

    def invalidate(self, memory_id: str):
        """当记忆更新时，使包含该记忆的缓存失效"""
        with self._lock:
            to_delete = [
                k for k, (mems, _) in self._cache.items()
                if any(m.id == memory_id for m in mems)
            ]
            for k in to_delete:
                del self._cache[k]
                if k in self._access_order:
                    self._access_order.remove(k)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "size": len(self._cache),
                "max_size": self.max_size,
                "keys": list(self._cache.keys())[:5],
                "hit_rate": "tracked separately"
            }


# ============================================================================
# Memory Scheduler（主类）
# ============================================================================

class MemoryScheduler:
    """
    记忆调度器

    核心方法：
        plan = scheduler.schedule(query, context)
        → 返回 RecallPlan，告诉 HMR 用什么策略、深度、是否用 JIT

    使用方式：
        # 在 HMR.recall() 里
        plan = self.scheduler.schedule(query, context)
        if plan.use_jit:
            result = self.jit_compiler.compile(query, context, top_k=plan.top_k)
        else:
            result = self.recall_engine.recall(query, context, top_k=plan.top_k)
    """

    def __init__(self, temporal_engine=None, memory_fs=None):
        self.temporal_engine = temporal_engine
        self.memory_fs = memory_fs
        self.hot_cache = HotCache(max_size=50, ttl_seconds=300)

        # 统计
        self._stats = {
            "total_scheduled": 0,
            "strategy_counts": {s.value: 0 for s in RecallStrategy},
            "cache_hits": 0,
            "cache_misses": 0,
        }

    def schedule(
        self,
        query: Optional[str],
        context: Dict[str, Any],
        hint: Optional[str] = None
    ) -> RecallPlan:
        """
        核心调度方法：分析查询和上下文，生成最优召回计划。

        决策逻辑：
            1. 有明确 hint → 直接用指定策略
            2. 查询复杂（多实体/推理型） → JIT 多步
            3. 时间相关查询 → TEMPORAL
            4. 有活跃运行时 + 明确目标 → HYBRID
            5. 简单查询 → SEMANTIC
        """
        self._stats["total_scheduled"] += 1

        # 优先用 hint
        if hint:
            return self._plan_from_hint(hint, query, context)

        # 分析查询特征
        query_str = query or context.get("active_goal", "")
        complexity = self._assess_complexity(query_str, context)
        is_temporal = self._is_temporal_query(query_str)
        has_active_runtime = bool(context.get("active_goal"))

        # 决策
        if complexity == "high":
            strategy = RecallStrategy.JIT
            use_jit = True
            top_k = 8
            steps = 3
            reasoning = f"查询复杂（多实体/推理），使用 JIT 多步检索"

        elif is_temporal:
            strategy = RecallStrategy.TEMPORAL
            use_jit = False
            top_k = 5
            steps = 1
            reasoning = "时间相关查询，优先时间权重排序"

        elif has_active_runtime and complexity == "medium":
            strategy = RecallStrategy.HYBRID
            use_jit = True
            top_k = 6
            steps = 2
            reasoning = f"有活跃目标（{context['active_goal'][:20]}），混合策略"

        else:
            strategy = RecallStrategy.SEMANTIC
            use_jit = False
            top_k = 5
            steps = 1
            reasoning = "简单语义查询，单次检索即可"

        self._stats["strategy_counts"][strategy.value] += 1

        return RecallPlan(
            strategy=strategy,
            top_k=top_k,
            use_jit=use_jit,
            jit_max_steps=steps,
            cache_result=(complexity != "high"),  # 复杂查询结果变化大，不缓存
            reasoning=reasoning
        )

    def get_from_cache(self, cache_key: str) -> Optional[List[MemoryObject]]:
        result = self.hot_cache.get(cache_key)
        if result:
            self._stats["cache_hits"] += 1
        else:
            self._stats["cache_misses"] += 1
        return result

    def put_to_cache(self, cache_key: str, memories: List[MemoryObject]):
        self.hot_cache.set(cache_key, memories)

    def invalidate_cache(self, memory_id: str):
        self.hot_cache.invalidate(memory_id)

    def get_stats(self) -> Dict[str, Any]:
        total = self._stats["total_scheduled"] or 1
        hit_rate = self._stats["cache_hits"] / max(
            self._stats["cache_hits"] + self._stats["cache_misses"], 1
        )
        return {
            **self._stats,
            "cache_hit_rate": f"{hit_rate:.1%}",
            "cache_stats": self.hot_cache.stats(),
            "dominant_strategy": max(
                self._stats["strategy_counts"],
                key=lambda k: self._stats["strategy_counts"][k]
            )
        }

    # -------------------------------------------------------------------------
    # 内部决策辅助
    # -------------------------------------------------------------------------

    def _assess_complexity(self, query: str, context: Dict[str, Any]) -> str:
        """评估查询复杂度：low / medium / high"""
        if not query:
            return "low"

        q_lower = query.lower()
        score = 0

        # 推理型关键词
        if any(w in q_lower for w in ["为什么", "原因", "why", "cause", "关系", "导致"]):
            score += 2
        # 多实体
        if any(w in q_lower for w in ["和", "与", "以及", "and", "both", "between"]):
            score += 1
        # 时间跨度
        if any(w in q_lower for w in ["历史", "演化", "变化", "history", "trend"]):
            score += 2
        # 上下文任务多
        pending = context.get("pending_tasks", [])
        if len(pending) > 3:
            score += 1
        # 查询本身较长
        if len(query) > 30:
            score += 1

        if score >= 4:
            return "high"
        elif score >= 2:
            return "medium"
        else:
            return "low"

    def _is_temporal_query(self, query: str) -> bool:
        """是否是时间相关查询"""
        temporal_words = [
            "最近", "昨天", "上次", "之前", "历史", "时间",
            "recent", "last", "previous", "history", "when", "timeline"
        ]
        q_lower = query.lower()
        return any(w in q_lower for w in temporal_words)

    def _plan_from_hint(self, hint: str, query: Optional[str], context: Dict) -> RecallPlan:
        """根据 hint 直接生成计划"""
        strategy_map = {
            "semantic": (RecallStrategy.SEMANTIC, False, 5, 1),
            "temporal": (RecallStrategy.TEMPORAL, False, 5, 1),
            "jit":      (RecallStrategy.JIT,      True,  8, 3),
            "hybrid":   (RecallStrategy.HYBRID,   True,  6, 2),
            "graph":    (RecallStrategy.GRAPH,     False, 5, 1),
        }
        strat, use_jit, top_k, steps = strategy_map.get(hint.lower(),
            (RecallStrategy.SEMANTIC, False, 5, 1))

        return RecallPlan(
            strategy=strat,
            top_k=top_k,
            use_jit=use_jit,
            jit_max_steps=steps,
            cache_result=True,
            reasoning=f"手动指定策略: {hint}"
        )
