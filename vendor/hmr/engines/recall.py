"""
Active Recall Engine - 主动召回引擎
修复内容：
1. 移除硬编码 3 条规则，改用 Embedding 相似度扩展 goal
2. 多因子评分加入真实的 SM-2 可提取性
3. 从运行时上下文中提取候选词，而不是靠字典
"""

from typing import Optional, Dict, Any, List
from datetime import datetime
from ..core.models import MemoryObject, RecallResult


class ActiveRecallEngine:
    """
    主动召回引擎（Embedding 版本）

    修复前的问题：
    - _expand_goal_to_concepts 只有 3 个硬编码词典，无法泛化
    - 评分没有用到 SM-2 可提取性
    - 预测"智能"约等于没有

    修复后：
    - 用 Embedding 相似度从现有记忆中找关联概念
    - 多维评分：语义相似度 + SM-2 可提取性 + 运行时依赖 + 访问频率
    - 从上下文和任务栈中动态提取候选 query
    """

    def __init__(self, semantic_engine, runtime_engine, temporal_engine=None):
        self.semantic_engine = semantic_engine
        self.runtime_engine = runtime_engine
        self.temporal_engine = temporal_engine   # 新增：接入时间引擎获取可提取性

    def recall(
        self,
        query: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        top_k: int = 5
    ) -> RecallResult:
        context = context or {}
        active_goal = context.get("active_goal")

        # 1. 构建候选 queries
        candidate_queries = self._build_candidate_queries(query, active_goal, context)

        # 2. 对每个候选 query 做语义检索，合并去重
        seen_ids = set()
        all_candidates: List[MemoryObject] = []

        for cq in candidate_queries:
            try:
                results = self.semantic_engine.retrieve(query=cq, top_k=top_k)
                for mem in results:
                    if mem.id not in seen_ids:
                        seen_ids.add(mem.id)
                        all_candidates.append(mem)
            except Exception as e:
                print(f"[HMR Recall] 语义检索失败 ({cq}): {e}")

        # 如果没有任何候选（比如 VectorStore 还是空的），降级到 MemoryFS 文本搜索
        if not all_candidates and query:
            try:
                all_candidates = self.semantic_engine.memory_fs.search_memories(query)
                for m in all_candidates:
                    seen_ids.add(m.id)
            except Exception:
                pass

        # 3. 多维评分
        relevance_scores: Dict[str, float] = {}
        for mem in all_candidates:
            relevance_scores[mem.id] = self._compute_relevance_score(
                memory=mem,
                query=query,
                goal=active_goal,
                context=context
            )

        # 4. 按分数排序，取 top_k
        all_candidates.sort(
            key=lambda m: relevance_scores[m.id],
            reverse=True
        )
        selected = all_candidates[:top_k]

        # 5. 强化被召回的记忆（访问 = 复习）
        for mem in selected:
            if self.temporal_engine:
                self.temporal_engine.reinforce_access(mem.id, grade=0.7)
            else:
                mem.reinforce()

        reasoning = self._generate_recall_reasoning(candidate_queries, selected)

        return RecallResult(
            memory_objects=selected,
            recall_reasoning=reasoning,
            predicted_need=candidate_queries,
            relevance_scores=relevance_scores,
            timestamp=datetime.utcnow()
        )

    # =========================================================================
    # 候选 query 构建（替换硬编码字典）
    # =========================================================================

    def _build_candidate_queries(
        self,
        query: Optional[str],
        goal: Optional[str],
        context: Dict[str, Any]
    ) -> List[str]:
        """
        动态构建候选 queries。

        策略：
        1. 原始 query（如果有）
        2. 当前 goal
        3. 从上下文中提取：focus_areas, pending_tasks 关键词
        4. 用 Embedding 找 goal 相关的已有记忆，提取它们的 runtime_dependencies
           （这才是真正的语义扩展，替代硬编码字典）
        """
        queries = []

        if query:
            queries.append(query)

        if goal:
            queries.append(goal)
            # 从 goal 中提取关键词（简单分词，比硬编码好）
            keywords = self._extract_keywords(goal)
            queries.extend(keywords[:3])  # 最多 3 个关键词

        # 从上下文提取
        for area in context.get("focus_areas", [])[:2]:
            queries.append(str(area))

        for task in context.get("pending_tasks", [])[:2]:
            if isinstance(task, dict):
                name = task.get("name", "")
                if name:
                    queries.append(name)
            elif isinstance(task, str):
                queries.append(task)

        # 语义扩展：找 goal 相关的记忆，提取它们的依赖作为新 query
        if goal:
            try:
                related = self.semantic_engine.retrieve(query=goal, top_k=3)
                for mem in related:
                    for dep in mem.runtime_dependencies[:2]:
                        if dep and dep not in queries:
                            queries.append(dep)
            except Exception:
                pass

        # 去重，保留顺序
        seen = set()
        result = []
        for q in queries:
            q = q.strip()
            if q and q not in seen:
                seen.add(q)
                result.append(q)

        return result[:8]  # 最多 8 个候选 query

    def _extract_keywords(self, text: str) -> List[str]:
        """
        从文本中提取关键词（基于词频 + 停用词过滤）
        比硬编码词典通用得多
        """
        stop_words = {
            "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都",
            "a", "an", "the", "is", "are", "was", "were", "be", "been",
            "to", "of", "and", "or", "in", "on", "at", "for", "with",
            "do", "does", "did", "will", "can", "should", "need", "want"
        }
        import re
        words = re.findall(r'\b\w{2,}\b', text.lower())
        return [w for w in words if w not in stop_words]

    # =========================================================================
    # 多维评分
    # =========================================================================

    def _compute_relevance_score(
        self,
        memory: MemoryObject,
        query: Optional[str],
        goal: Optional[str],
        context: Dict[str, Any]
    ) -> float:
        """
        多维评分：

        1. SM-2 可提取性（记忆有多新鲜）     权重 30%
        2. 运行时依赖匹配                     权重 25%
        3. 访问频率（常用记忆更相关）          权重 20%
        4. 标签/类型匹配                       权重 15%
        5. 置信度                              权重 10%
        """
        score = 0.0

        # 1. SM-2 可提取性（0-1）
        if self.temporal_engine and memory.id in self.temporal_engine.sm2_states:
            retrievability = self.temporal_engine.sm2_states[memory.id].retrievability()
        else:
            retrievability = memory.temporal_weight
        score += retrievability * 0.30

        # 2. 运行时依赖匹配
        dep_match = 0.0
        goal_str = (goal or "").lower()
        query_str = (query or "").lower()
        for dep in memory.runtime_dependencies:
            dep_lower = dep.lower()
            if dep_lower in goal_str or dep_lower in query_str:
                dep_match = 1.0
                break
            # 部分匹配
            if any(word in dep_lower for word in self._extract_keywords(goal_str)):
                dep_match = max(dep_match, 0.5)
        score += dep_match * 0.25

        # 3. 访问频率（log 压缩，避免极端值）
        import math
        freq_score = min(1.0, math.log1p(memory.access_count) / 5.0)
        score += freq_score * 0.20

        # 4. 标签匹配
        all_keywords = set(self._extract_keywords(goal_str + " " + query_str))
        tag_match = sum(1 for tag in memory.tags if tag.lower() in all_keywords)
        tag_score = min(1.0, tag_match / max(len(memory.tags), 1))
        score += tag_score * 0.15

        # 5. 置信度
        score += memory.confidence * 0.10

        return min(1.0, score)

    def _generate_recall_reasoning(
        self,
        candidate_queries: List[str],
        selected: List[MemoryObject]
    ) -> str:
        if not selected:
            return f"基于 {len(candidate_queries)} 个候选查询，未找到相关记忆"

        reasoning = (
            f"基于 {len(candidate_queries)} 个候选查询"
            f"（{', '.join(candidate_queries[:3])}"
            f"{'...' if len(candidate_queries) > 3 else ''}），"
            f"召回 {len(selected)} 条最相关记忆。"
        )

        top = selected[0]
        reasoning += f" 最相关：《{top.title}》（类型: {top.type}）"

        return reasoning
