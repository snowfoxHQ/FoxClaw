"""
Self-Evolution Engine - 自我演化引擎
HMR v2.0 新增

解决的问题：
  记忆库随时间膨胀，重复信息累积，结构越来越混乱
  Self-Evolution Engine 让 HMR 主动优化自身：
    1. PatternDetector    → 发现重复/矛盾/知识空白
    2. KnowledgeAbstractor → 把相似记忆提炼为更高阶的抽象概念
    3. ContradictionResolver → 处理互相矛盾的记忆
    4. StrategyOptimizer  → 分析使用模式，推荐 LifecycleConfig 优化参数

演化是渐进的、保守的：
  - 不主动删除记忆（只降低权重）
  - 每次只做小步改变
  - 每次演化都记录操作日志（可追溯）
"""

import re
import math
import json
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Set
from dataclasses import dataclass, field
from datetime import datetime
from collections import Counter, defaultdict


# ============================================================================
# 演化日志
# ============================================================================

@dataclass
class EvolutionLog:
    """一次演化操作的记录"""
    operation: str                   # abstract / resolve / optimize / cluster
    affected_ids: List[str]          # 受影响的记忆 ID
    result_id: Optional[str]         # 产生的新记忆 ID（如果有）
    description: str
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "operation": self.operation,
            "affected_ids": self.affected_ids,
            "result_id": self.result_id,
            "description": self.description,
            "timestamp": self.timestamp.isoformat(),
        }


# ============================================================================
# Pattern Detector（模式识别）
# ============================================================================

class PatternDetector:
    """
    在记忆库中发现模式：
      - 高重复率的记忆簇（适合抽象）
      - 互相矛盾的记忆（需要解决）
      - 知识空白（高频查询但无对应记忆）
    """

    def find_duplicate_clusters(
        self,
        memories,
        vector_store=None,
        similarity_threshold: float = 0.75,
        min_cluster_size: int = 3
    ) -> List[List[Any]]:
        """
        找出相似度 > threshold 的记忆簇（适合压缩抽象）

        使用向量余弦相似度（有 VectorStore 时），
        降级到标题词重叠（无向量时）。
        """
        assigned: Set[str] = set()

        if vector_store and len(vector_store.vectors) > 0:
            return self._cluster_by_vector(
                memories, vector_store, similarity_threshold, min_cluster_size, assigned
            )
        else:
            return self._cluster_by_text(memories, min_cluster_size)

    def _cluster_by_vector(
        self, memories, vector_store, threshold, min_size, assigned
    ) -> List[List[Any]]:
        clusters = []
        mem_list = [m for m in memories if m.id in vector_store.vectors]

        for i, mem_a in enumerate(mem_list):
            if mem_a.id in assigned:
                continue
            cluster = [mem_a]
            vec_a = vector_store.vectors[mem_a.id]

            for mem_b in mem_list[i+1:]:
                if mem_b.id in assigned or mem_b.type != mem_a.type:
                    continue
                vec_b = vector_store.vectors.get(mem_b.id)
                if vec_b is None:
                    continue
                sim = self._cosine(vec_a, vec_b)
                if sim >= threshold:
                    cluster.append(mem_b)

            if len(cluster) >= min_size:
                for m in cluster:
                    assigned.add(m.id)
                clusters.append(cluster)

        return clusters

    def _cluster_by_text(self, memories, min_size: int) -> List[List[Any]]:
        """标题词重叠聚类（降级方案）"""
        by_type: Dict[str, List[Any]] = defaultdict(list)
        for m in memories:
            by_type[m.type].append(m)

        clusters = []
        for mems in by_type.values():
            if len(mems) >= min_size:
                # 按标题相似度进一步分组
                groups: Dict[str, List[Any]] = defaultdict(list)
                for m in mems:
                    key_words = frozenset(
                        re.findall(r'\b\w{3,}\b', m.title.lower())[:3]
                    )
                    groups[str(sorted(key_words))].append(m)
                for g in groups.values():
                    if len(g) >= min_size:
                        clusters.append(g)
        return clusters

    def find_contradictions(self, memories) -> List[Tuple[Any, Any, str]]:
        """
        找出互相矛盾的记忆对。

        简单规则：
        - 同一主题（标题词重叠 > 60%），但 confidence 差异 > 0.4
        - 包含明确的否定词对：
            ("不应该", "应该"), ("避免", "推荐"), ("失败", "成功")
        """
        contradictions = []
        positive = {"应该", "推荐", "最佳", "成功", "有效", "recommend", "best", "success"}
        negative = {"不应该", "避免", "失败", "禁止", "avoid", "fail", "never", "wrong"}

        mem_list = list(memories)
        for i, ma in enumerate(mem_list):
            for mb in mem_list[i+1:]:
                if ma.type != mb.type:
                    continue
                # 标题重叠
                words_a = set(re.findall(r'\b\w{3,}\b', ma.title.lower()))
                words_b = set(re.findall(r'\b\w{3,}\b', mb.title.lower()))
                if not words_a or not words_b:
                    continue
                overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
                if overlap < 0.4:
                    continue

                # 语义方向相反
                text_a = (ma.content + ma.title).lower()
                text_b = (mb.content + mb.title).lower()
                a_pos = any(w in text_a for w in positive)
                a_neg = any(w in text_a for w in negative)
                b_pos = any(w in text_b for w in positive)
                b_neg = any(w in text_b for w in negative)

                if (a_pos and b_neg) or (a_neg and b_pos):
                    contradictions.append((
                        ma, mb,
                        f"方向相反：A={'肯定' if a_pos else '否定'}，"
                        f"B={'肯定' if b_pos else '否定'}"
                    ))

                # 置信度差异大（同主题不同结论）
                elif abs(ma.confidence - mb.confidence) > 0.4 and overlap > 0.6:
                    contradictions.append((
                        ma, mb,
                        f"置信度矛盾：A={ma.confidence:.2f}，B={mb.confidence:.2f}"
                    ))

        return contradictions[:20]  # 最多返回 20 对

    def find_knowledge_gaps(
        self,
        memories,
        recall_history: List[Dict[str, Any]],
        min_query_count: int = 3
    ) -> List[str]:
        """
        找出知识空白：频繁被查询但没有对应记忆的主题。

        recall_history 格式：[{"query": str, "hit_count": int, "miss": bool}]
        """
        # 统计未命中的查询
        miss_queries: Counter = Counter()
        for event in recall_history:
            if event.get("miss"):
                miss_queries[event["query"]] += 1

        # 找出频繁失败的查询
        gaps = []
        for query, count in miss_queries.most_common(10):
            if count >= min_query_count:
                # 检查是否真的没有对应记忆
                has_memory = any(
                    query.lower() in (m.title + m.content).lower()
                    for m in memories
                )
                if not has_memory:
                    gaps.append(f"知识空白：'{query}'（查询{count}次，无对应记忆）")

        return gaps

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if na and nb else 0.0


# ============================================================================
# Knowledge Abstractor（知识抽象）
# ============================================================================

class KnowledgeAbstractor:
    """
    把相似/重复的记忆提炼为更高阶的抽象概念。

    两种抽象模式：
    1. 归纳抽象：多个 execution 记录 → 一条 insight（规律）
    2. 概念提升：多个 concept → 一条更高阶 concept
    """

    def abstract_cluster(
        self,
        memories,
        llm_fn=None
    ) -> Optional[Dict[str, Any]]:
        """
        将一组相似记忆抽象为一条新记忆。

        返回：适合传给 hmr.ingest 的参数 dict，或 None（如果不值得抽象）
        """
        if len(memories) < 2:
            return None

        mtypes = [m.type for m in memories]
        dominant_type = Counter(mtypes).most_common(1)[0][0]

        # 根据类型选择目标类型
        target_type = {
            "execution": "reflection",   # 执行记录 → 反思洞察
            "concept":   "concept",      # 概念 → 更高阶概念
            "decision":  "concept",      # 决策 → 决策模式
            "task":      "workflow",     # 任务 → 工作流
        }.get(dominant_type, "concept")

        # 生成内容
        content = self._generate_abstract(memories, dominant_type, llm_fn)
        if not content:
            return None

        # 合并所有标签和依赖
        all_tags = list(set(t for m in memories for t in m.tags))[:8]
        all_deps = list(set(d for m in memories for d in m.runtime_dependencies))[:5]
        avg_confidence = sum(m.confidence for m in memories) / len(memories)

        return {
            "content": content,
            "memory_type": target_type,
            "title": f"[抽象] {self._extract_topic(memories)}",
            "metadata": {
                "tags": all_tags + ["abstracted", f"from_{len(memories)}_memories"],
                "runtime_dependencies": all_deps,
                "confidence": round(min(0.95, avg_confidence + 0.1), 2),
            }
        }

    def _generate_abstract(
        self, memories, dominant_type: str, llm_fn=None
    ) -> Optional[str]:
        """生成抽象内容（LLM 或 TF-IDF）"""
        if llm_fn:
            try:
                combined = "\n---\n".join(
                    f"[{m.type}] {m.title}: {m.content[:200]}"
                    for m in memories
                )
                return llm_fn(combined, len(memories))
            except Exception:
                pass

        # TF-IDF 降级
        return self._tfidf_abstract(memories, dominant_type)

    def _tfidf_abstract(self, memories, dominant_type: str) -> str:
        stop = {"的","了","是","在","a","an","the","is","to","of","and","in","for"}
        all_text = " ".join(m.content for m in memories)
        words = re.findall(r'\b\w{2,}\b', all_text.lower())
        kws = [w for w in words if w not in stop]
        top_kws = [w for w, _ in Counter(kws).most_common(10)]

        type_prefix = {
            "execution": "综合执行经验规律",
            "concept":   "抽象知识提炼",
            "decision":  "决策模式总结",
        }.get(dominant_type, "知识抽象")

        return (
            f"{type_prefix}（来自 {len(memories)} 条记忆）：\n"
            f"核心主题：{', '.join(top_kws[:6])}。\n"
            f"时间跨度：{memories[0].created_at.date()} ~ {memories[-1].created_at.date()}。"
        )

    def _extract_topic(self, memories) -> str:
        words = []
        for m in memories:
            words.extend(re.findall(r'\b\w{3,}\b', m.title))
        if not words:
            return "综合记忆"
        top = Counter(words).most_common(2)
        return " + ".join(w for w, _ in top)


# ============================================================================
# Contradiction Resolver（矛盾解决）
# ============================================================================

class ContradictionResolver:
    """
    处理互相矛盾的记忆对。

    策略：
    - 保留置信度更高的那条（主要版本）
    - 降低另一条的置信度和 temporal_weight
    - 创建一条新的 reflection 记忆记录这个矛盾和解决过程
    """

    def resolve(
        self,
        mem_a,
        mem_b,
        reason: str,
        memory_fs=None
    ) -> Dict[str, Any]:
        """
        解决两条记忆之间的矛盾。

        返回：
        {
            "winner": mem,        # 保留的记忆
            "loser": mem,         # 降权的记忆
            "reflection_content": str  # 建议存储的矛盾说明
        }
        """
        # 根据置信度 + 访问频率决定哪条是"正确的"
        score_a = mem_a.confidence * 0.6 + min(1.0, mem_a.access_count / 10) * 0.4
        score_b = mem_b.confidence * 0.6 + min(1.0, mem_b.access_count / 10) * 0.4

        winner, loser = (mem_a, mem_b) if score_a >= score_b else (mem_b, mem_a)

        # 降低 loser 的权重
        loser.confidence = max(0.1, loser.confidence * 0.5)
        loser.temporal_weight = max(0.1, loser.temporal_weight * 0.5)
        if "contradicted" not in loser.tags:
            loser.tags.append("contradicted")

        if memory_fs:
            memory_fs.write_memory(loser)

        reflection_content = (
            f"发现矛盾记忆对：\n"
            f"  保留：[{winner.type}]《{winner.title}》（置信度={winner.confidence:.2f}）\n"
            f"  降权：[{loser.type}]《{loser.title}》\n"
            f"  原因：{reason}"
        )

        return {
            "winner": winner,
            "loser": loser,
            "reflection_content": reflection_content
        }


# ============================================================================
# Strategy Optimizer（策略优化器）
# ============================================================================

class StrategyOptimizer:
    """
    分析 HMR 的使用模式，推荐优化参数。
    """

    def suggest_lifecycle_config(
        self,
        memory_stats: Dict[str, Any],
        scheduler_stats: Dict[str, Any],
        lifecycle_stats: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        分析当前系统状态，推荐 LifecycleConfig 参数。
        """
        suggestions = {}
        reasons = []

        total = memory_stats.get("total_memories", 0)
        by_type = memory_stats.get("memory_types", {})
        dominant_strategy = scheduler_stats.get("dominant_strategy", "semantic")
        hit_rate = float(scheduler_stats.get("cache_hit_rate", "0%").rstrip("%")) / 100
        dormant = lifecycle_stats.get("by_state", {}).get("dormant", 0)

        # 1. max_memories_per_type
        max_per_type = max(by_type.values(), default=0)
        if max_per_type > 150:
            suggestions["max_memories_per_type"] = 80
            reasons.append(f"某类型记忆数 {max_per_type} 过多，建议降低阈值到 80")
        elif max_per_type < 20:
            suggestions["max_memories_per_type"] = 200
            reasons.append("记忆数量较少，放宽压缩限制")

        # 2. check_interval_ingests
        if total > 500:
            suggestions["check_interval_ingests"] = 5
            reasons.append("记忆库较大，建议加密检查频率（每5次）")
        elif total < 50:
            suggestions["check_interval_ingests"] = 20
            reasons.append("记忆库较小，降低检查频率（每20次）")

        # 3. prune_retrievability
        if dormant > total * 0.3:
            suggestions["prune_retrievability"] = 0.08
            reasons.append(f"休眠记忆占比 {dormant/max(total,1):.0%}，适当提高删除阈值")

        # 4. JIT 相关
        if dominant_strategy == "jit" and hit_rate < 0.4:
            reasons.append("JIT 使用多但命中率低，建议检查查询质量或增加记忆数量")

        return {
            "suggested_config": suggestions,
            "reasons": reasons,
            "current_health": self._compute_health(total, hit_rate, dormant)
        }

    def analyze_recall_patterns(
        self, scheduler_stats: Dict[str, Any]
    ) -> List[str]:
        """分析召回使用模式，给出优化建议"""
        insights = []
        counts = scheduler_stats.get("strategy_counts", {})
        hit_rate = scheduler_stats.get("cache_hit_rate", "0%")

        total_calls = sum(counts.values())
        if total_calls == 0:
            return ["尚无足够数据进行分析"]

        for strat, cnt in counts.items():
            ratio = cnt / total_calls
            if ratio > 0.6:
                insights.append(f"{strat} 策略占主导（{ratio:.0%}），系统查询风格一致")
            elif ratio > 0.3:
                insights.append(f"{strat} 策略频繁使用（{ratio:.0%}）")

        hit = float(str(hit_rate).rstrip("%")) / 100
        if hit > 0.5:
            insights.append(f"热缓存命中率 {hit_rate}，查询重复度高，可考虑扩大缓存容量")
        elif hit < 0.2:
            insights.append(f"热缓存命中率 {hit_rate}，查询多样性高，缓存效益有限")

        return insights

    def _compute_health(self, total: int, hit_rate: float, dormant: int) -> str:
        if total == 0:
            return "空库"
        dormant_ratio = dormant / total
        if dormant_ratio > 0.5:
            return "⚠️ 记忆老化严重，建议运行压缩"
        elif dormant_ratio > 0.3:
            return "⚡ 一般，有一定老化"
        elif hit_rate > 0.4:
            return "✅ 健康，缓存效率良好"
        else:
            return "✅ 健康"


# ============================================================================
# Self-Evolution Engine（主类）
# ============================================================================

class SelfEvolutionEngine:
    """
    自我演化引擎

    用法：
        evo = SelfEvolutionEngine(storage_path="./hmr_data/evolution")
        evo.register_ingest_fn(hmr.ingest)

        # 运行完整演化周期
        report = evo.evolve(
            memories=hmr.memory_fs.list_memories(),
            vector_store=hmr.vector_store,
            memory_fs=hmr.memory_fs,
        )

        print(report["summary"])
        for action in report["actions"]:
            print(f"  {action}")
    """

    def __init__(self, storage_path: Optional[str] = None):
        self.storage_path = Path(storage_path) if storage_path else None
        self._lock = threading.Lock()

        self.detector   = PatternDetector()
        self.abstractor = KnowledgeAbstractor()
        self.resolver   = ContradictionResolver()
        self.optimizer  = StrategyOptimizer()

        self.logs: List[EvolutionLog] = []
        self._ingest_fn = None
        self._llm_fn = None

        if self.storage_path:
            self.storage_path.mkdir(parents=True, exist_ok=True)
            self._load_logs()

    def register_ingest_fn(self, fn):
        self._ingest_fn = fn

    def register_llm_fn(self, fn):
        """注入 LLM 摘要函数（可选）"""
        self._llm_fn = fn

    def evolve(
        self,
        memories,
        vector_store=None,
        memory_fs=None,
        system_stats: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        运行一次完整的演化周期。

        dry_run=True：只分析，不实际修改（用于预览）

        返回演化报告：
        {
            "summary": str,
            "actions": List[str],
            "abstractions": int,
            "contradictions_resolved": int,
            "suggestions": dict,
            "logs": List[EvolutionLog]
        }
        """
        mem_list = list(memories)
        actions = []
        new_logs = []
        abstractions = 0
        contradictions_resolved = 0

        print(f"[Evolution] 开始演化，记忆库 {len(mem_list)} 条...")

        # ── Phase 1：发现重复簇并抽象 ─────────────────────────────────────
        clusters = self.detector.find_duplicate_clusters(
            mem_list, vector_store, similarity_threshold=0.75, min_cluster_size=3
        )
        print(f"[Evolution] 发现 {len(clusters)} 个重复簇")

        for cluster in clusters[:5]:  # 每次最多处理 5 个簇
            abstract_kwargs = self.abstractor.abstract_cluster(
                cluster, llm_fn=self._llm_abstract_fn()
            )
            if abstract_kwargs and not dry_run and self._ingest_fn:
                new_mem = self._ingest_fn(**abstract_kwargs)
                log = EvolutionLog(
                    operation="abstract",
                    affected_ids=[m.id for m in cluster],
                    result_id=new_mem.id,
                    description=f"抽象 {len(cluster)} 条 → 《{abstract_kwargs['title']}》"
                )
                new_logs.append(log)
                actions.append(f"✅ 抽象 {len(cluster)} 条 [{cluster[0].type}] → 《{abstract_kwargs['title']}》")
                abstractions += 1
            elif dry_run:
                actions.append(f"[预览] 可抽象 {len(cluster)} 条 [{cluster[0].type}] 记忆")

        # ── Phase 2：检测并处理矛盾 ────────────────────────────────────────
        contradictions = self.detector.find_contradictions(mem_list)
        print(f"[Evolution] 发现 {len(contradictions)} 对矛盾记忆")

        for mem_a, mem_b, reason in contradictions[:5]:
            if not dry_run:
                result = self.resolver.resolve(mem_a, mem_b, reason, memory_fs)
                # 存储矛盾说明到记忆
                if self._ingest_fn:
                    self._ingest_fn(
                        content=result["reflection_content"],
                        memory_type="reflection",
                        title=f"[矛盾解决] {mem_a.title[:20]}",
                        metadata={"tags": ["contradiction", "resolved"], "confidence": 0.8}
                    )
                log = EvolutionLog(
                    operation="resolve",
                    affected_ids=[mem_a.id, mem_b.id],
                    result_id=None,
                    description=f"解决矛盾：{reason}"
                )
                new_logs.append(log)
                actions.append(f"🔧 解决矛盾：《{mem_a.title[:25]}》vs《{mem_b.title[:25]}》（{reason}）")
                contradictions_resolved += 1
            else:
                actions.append(f"[预览] 矛盾：《{mem_a.title[:25]}》vs《{mem_b.title[:25]}》")

        # ── Phase 3：策略优化建议 ──────────────────────────────────────────
        suggestions = {}
        if system_stats:
            suggestions = self.optimizer.suggest_lifecycle_config(
                memory_stats=system_stats.get("memory_fs", {}),
                scheduler_stats=system_stats.get("scheduler", {}),
                lifecycle_stats=system_stats.get("lifecycle", {}),
            )
            if suggestions.get("reasons"):
                for r in suggestions["reasons"]:
                    actions.append(f"💡 建议：{r}")

        # ── 保存日志 ───────────────────────────────────────────────────────
        with self._lock:
            self.logs.extend(new_logs)

        if not dry_run and self.storage_path:
            self._save_logs()

        summary = (
            f"演化完成：抽象 {abstractions} 个簇，"
            f"解决 {contradictions_resolved} 对矛盾，"
            f"{'健康度: ' + suggestions.get('current_health', '') if suggestions else ''}"
        )
        print(f"[Evolution] {summary}")

        return {
            "summary": summary,
            "actions": actions,
            "abstractions": abstractions,
            "contradictions_resolved": contradictions_resolved,
            "suggestions": suggestions,
            "logs": new_logs,
            "dry_run": dry_run,
        }

    def get_stats(self) -> Dict[str, Any]:
        by_op: Dict[str, int] = Counter(log.operation for log in self.logs)
        return {
            "total_operations": len(self.logs),
            "by_operation": dict(by_op),
            "recent_operations": [
                {"op": l.operation, "desc": l.description, "time": l.timestamp.isoformat()}
                for l in self.logs[-5:]
            ]
        }

    def _llm_abstract_fn(self):
        """返回 LLM 抽象函数（如果有注入）"""
        if not self._llm_fn:
            return None
        llm = self._llm_fn

        def fn(combined: str, count: int) -> Optional[str]:
            try:
                return llm(
                    f"从以下 {count} 条记忆中提炼核心规律（150字以内）：\n{combined[:2000]}"
                )
            except Exception:
                return None
        return fn

    def _save_logs(self):
        if not self.storage_path:
            return
        path = self.storage_path / "evolution_logs.json"
        tmp = path.with_suffix(".tmp")
        data = [l.to_dict() for l in self.logs[-500:]]
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        tmp.replace(path)

    def _load_logs(self):
        path = self.storage_path / "evolution_logs.json"
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for d in data:
                self.logs.append(EvolutionLog(
                    operation=d["operation"],
                    affected_ids=d["affected_ids"],
                    result_id=d.get("result_id"),
                    description=d["description"],
                    timestamp=datetime.fromisoformat(d["timestamp"])
                ))
            print(f"[Evolution] 加载 {len(self.logs)} 条演化日志")
        except Exception as e:
            print(f"[Evolution] 日志加载失败: {e}")
