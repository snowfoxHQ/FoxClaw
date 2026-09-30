"""
Memory Lifecycle Engine - 记忆生命周期引擎
HMR v1.5 新增

解决问题：
- compress_memories 需要手动调用 → 自动触发
- 低价值记忆持续占用空间 → 自动 prune
- 同类记忆堆积 → 自动 consolidate

生命周期状态：
  新生(fresh) → 活跃(active) → 衰退(fading) → 休眠(dormant) → 删除(pruned)
                                                        ↘ 合并(consolidated)
"""

import threading
from typing import List, Dict, Optional, Callable, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from ..core.models import MemoryObject


@dataclass
class LifecycleConfig:
    """生命周期配置"""
    # 触发条件
    max_memories_per_type: int = 80       # 同类型超过此数触发压缩
    prune_retrievability: float = 0.05    # SM-2 可提取性低于此值考虑删除
    prune_min_age_days: int = 7           # 至少存在 7 天才允许 prune
    prune_require_zero_access: bool = True  # 必须从未被访问才 prune

    # 压缩参数
    consolidation_batch: int = 20         # 每次压缩多少条
    consolidation_keep_ratio: float = 0.3 # 压缩后保留原记忆的 30%（降低权重而非删除）

    # 自动运行
    auto_enabled: bool = True
    check_interval_ingests: int = 10      # 每 10 次 ingest 检查一次（避免每次都跑）


@dataclass
class LifecycleReport:
    """生命周期执行报告"""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    pruned_count: int = 0
    consolidated_count: int = 0
    compressed_to: int = 0                # 压缩后生成了几条新记忆
    checked_count: int = 0
    reasons: List[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"检查 {self.checked_count} 条"]
        if self.pruned_count:
            parts.append(f"删除 {self.pruned_count} 条")
        if self.consolidated_count:
            parts.append(f"压缩 {self.consolidated_count} → {self.compressed_to} 条")
        return "；".join(parts) if parts else "无操作"


class MemoryLifecycleEngine:
    """
    记忆生命周期引擎

    在 HMR 主引擎里，每 N 次 ingest 后自动调用：
        lifecycle.check(triggered_by=new_memory)

    核心决策流：
        ┌──────────────┐
        │  所有记忆     │
        └──────┬───────┘
               ↓
        ┌──────────────┐   可提取性 < 0.05
        │  衰退检测    │ ──────────────────→  pruned（删除）
        └──────┬───────┘
               │ 未到删除线
               ↓
        ┌──────────────┐   同类型 > max
        │  数量检测    │ ──────────────────→  consolidate（压缩）
        └──────┬───────┘
               │ 数量正常
               ↓
              保留
    """

    def __init__(self, memory_fs, temporal_engine, config: LifecycleConfig = None):
        self.memory_fs = memory_fs
        self.temporal_engine = temporal_engine
        self.config = config or LifecycleConfig()
        self._lock = threading.Lock()
        self._ingest_counter = 0
        self._compress_fn: Optional[Callable] = None  # 注入 hmr.compress_memories

    def register_compress_fn(self, fn: Callable):
        """注入压缩函数（避免循环引用）"""
        self._compress_fn = fn

    def on_ingest(self, new_memory: MemoryObject) -> Optional[LifecycleReport]:
        """
        每次 ingest 后调用。
        根据 check_interval_ingests 决定是否真正执行检查。
        """
        if not self.config.auto_enabled:
            return None

        with self._lock:
            self._ingest_counter += 1
            should_check = (self._ingest_counter % self.config.check_interval_ingests == 0)

        if not should_check:
            return None

        # 异步执行，不阻塞 ingest
        report = LifecycleReport()
        t = threading.Thread(
            target=self._run_check,
            args=(new_memory.type, report),
            daemon=True
        )
        t.start()
        return report   # report 会在后台填充

    def check_now(self, memory_type: Optional[str] = None) -> LifecycleReport:
        """手动触发完整检查（同步）"""
        report = LifecycleReport()
        self._run_check(memory_type, report)
        return report

    def _run_check(self, memory_type: Optional[str], report: LifecycleReport):
        """执行生命周期检查（在后台线程中运行）"""
        try:
            # 1. Prune：删除低价值记忆
            pruned = self._prune(memory_type, report)

            # 2. Consolidate：压缩数量过多的记忆
            self._consolidate(memory_type, report)

        except Exception as e:
            report.reasons.append(f"生命周期检查失败: {e}")
            print(f"[HMR Lifecycle] 错误: {e}")

    def _prune(self, memory_type: Optional[str], report: LifecycleReport) -> List[str]:
        """删除满足条件的低价值记忆"""
        memories = self.memory_fs.list_memories(memory_type)
        report.checked_count = len(memories)
        pruned_ids = []
        now = datetime.utcnow()

        for mem in memories:
            # 条件 1：存在足够长时间
            age = (now - mem.created_at).days
            if age < self.config.prune_min_age_days:
                continue

            # 条件 2：从未被访问（如果配置要求）
            if self.config.prune_require_zero_access and mem.access_count > 0:
                continue

            # 条件 3：SM-2 可提取性极低
            if mem.id in self.temporal_engine.sm2_states:
                r = self.temporal_engine.sm2_states[mem.id].retrievability()
            else:
                r = mem.temporal_weight

            if r < self.config.prune_retrievability:
                # 执行删除
                deleted = self.memory_fs.delete_memory(mem.id)
                if deleted:
                    pruned_ids.append(mem.id)
                    report.pruned_count += 1
                    report.reasons.append(
                        f"删除 [{mem.type}]《{mem.title}》"
                        f"（可提取性={r:.3f}，存在{age}天，访问0次）"
                    )

        if pruned_ids:
            print(f"[HMR Lifecycle] 自动删除 {len(pruned_ids)} 条低价值记忆")

        return pruned_ids

    def _consolidate(self, memory_type: Optional[str], report: LifecycleReport):
        """当同类型记忆超过阈值时自动压缩"""
        if not self._compress_fn:
            return

        types_to_check = [memory_type] if memory_type else [
            "execution", "reflection", "agent_memory"
        ]

        for mtype in types_to_check:
            if not mtype:
                continue
            memories = self.memory_fs.list_memories(mtype)

            if len(memories) <= self.config.max_memories_per_type:
                continue

            # 找出最老/最不活跃的一批来压缩
            candidates = sorted(
                memories,
                key=lambda m: (m.temporal_weight, m.access_count)
            )[:self.config.consolidation_batch]

            if len(candidates) < 3:
                continue

            print(f"[HMR Lifecycle] [{mtype}] 记忆数={len(memories)}，自动压缩 {len(candidates)} 条...")

            try:
                compressed = self._compress_fn(
                    memory_ids=[m.id for m in candidates]
                )
                if compressed:
                    report.consolidated_count += len(candidates)
                    report.compressed_to += 1
                    report.reasons.append(
                        f"[{mtype}] {len(candidates)} 条 → 1 条《{compressed.title}》"
                    )
            except Exception as e:
                print(f"[HMR Lifecycle] 压缩失败: {e}")

    def get_lifecycle_stats(self) -> Dict[str, Any]:
        """获取记忆生命周期统计"""
        all_memories = self.memory_fs.list_memories()
        now = datetime.utcnow()

        stats = {
            "total": len(all_memories),
            "by_state": {"fresh": 0, "active": 0, "fading": 0, "dormant": 0},
            "by_type": {},
            "at_risk": []  # 即将被 prune 的记忆
        }

        for mem in all_memories:
            # 获取可提取性
            if mem.id in self.temporal_engine.sm2_states:
                r = self.temporal_engine.sm2_states[mem.id].retrievability()
            else:
                r = mem.temporal_weight

            # 分类状态
            age = (now - mem.created_at).days
            if age < 1:
                state = "fresh"
            elif r > 0.7:
                state = "active"
            elif r > 0.3:
                state = "fading"
            else:
                state = "dormant"

            stats["by_state"][state] += 1
            stats["by_type"][mem.type] = stats["by_type"].get(mem.type, 0) + 1

            # 标记风险记忆
            if r < self.config.prune_retrievability * 2 and mem.access_count == 0:
                stats["at_risk"].append({
                    "id": mem.id,
                    "title": mem.title,
                    "type": mem.type,
                    "retrievability": round(r, 3),
                    "age_days": age
                })

        stats["at_risk"] = stats["at_risk"][:10]  # 最多显示 10 条
        return stats
