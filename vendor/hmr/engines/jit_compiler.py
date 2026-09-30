"""
JIT Memory Compiler - 即时记忆编译器
HMR v1.5 新增

把 single-shot RAG 升级为多步推理检索

流程：
  Query
    ↓
  QueryAnalyzer（意图分类）
    ↓
  SubQueryPlanner（拆解子查询）
    ↓
  Step 1 检索 → GapAnalyzer（找缺口）
    ↓
  QueryRewriter（基于缺口改写）
    ↓
  Step 2 检索 → 再次分析
    ↓（最多 3 层）
  MemoryStitcher（拼接去重排序）
    ↓
  CompileResult
"""

import re
import os
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from ..core.models import MemoryObject


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class QueryIntent:
    """查询意图分类"""
    type: str            # factual / procedural / contextual / exploratory
    complexity: str      # simple / multi-hop / deep
    entities: List[str]  # 提取到的实体
    sub_queries: List[str]  # 拆解的子查询


@dataclass
class RetrievalStep:
    """单步检索结果"""
    step: int
    query: str
    memories: List[MemoryObject]
    gaps: List[str]      # 本步结果里缺少什么
    confidence: float    # 本步结果的置信度


@dataclass
class CompileResult:
    """JIT 编译完整结果"""
    memories: List[MemoryObject]             # 最终去重排序的记忆列表
    steps: List[RetrievalStep]               # 每步检索记录
    query_trace: List[str]                   # 查询改写轨迹
    relevance_scores: Dict[str, float]       # memory_id → 分数
    reasoning: str                           # 编译推理说明


# ============================================================================
# Query Analyzer（意图分析）
# ============================================================================

class QueryAnalyzer:
    """
    分析查询意图，决定需要几步检索、怎么拆解。

    优先用 LLM，降级到规则分析。
    """

    COMPLEXITY_KEYWORDS = {
        "multi-hop": ["为什么", "原因", "怎么导致", "how did", "why did", "what caused",
                      "关系", "之间", "与.*的关系", "compare", "difference"],
        "deep":      ["详细", "完整", "所有", "全部", "历史", "演化", "timeline",
                      "everything", "all", "complete", "history"],
        "simple":    []
    }

    def analyze(self, query: str, context: Dict[str, Any] = None) -> QueryIntent:
        """分析查询意图"""
        # 尝试 LLM 分析
        llm_result = self._analyze_with_llm(query, context)
        if llm_result:
            return llm_result

        # 规则降级
        return self._analyze_with_rules(query, context)

    def _analyze_with_llm(self, query: str, context: Dict[str, Any]) -> Optional[QueryIntent]:
        try:
            import openai, json
            key = os.environ.get("OPENAI_API_KEY", "")
            if not key:
                return None

            client = openai.OpenAI(api_key=key)
            ctx_str = f"当前目标：{context.get('active_goal', '未知')}" if context else ""

            prompt = f"""分析以下查询的意图，返回 JSON：
查询：{query}
{ctx_str}

返回格式（只返回 JSON，不要其他内容）：
{{
  "type": "factual|procedural|contextual|exploratory",
  "complexity": "simple|multi-hop|deep",
  "entities": ["实体1", "实体2"],
  "sub_queries": ["子查询1", "子查询2"]
}}"""

            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0
            )
            data = json.loads(resp.choices[0].message.content.strip())
            return QueryIntent(**data)
        except Exception:
            return None

    def _analyze_with_rules(self, query: str, context: Dict[str, Any] = None) -> QueryIntent:
        """规则分析（无需 API）"""
        q_lower = query.lower()

        # 判断复杂度
        complexity = "simple"
        for level, keywords in self.COMPLEXITY_KEYWORDS.items():
            if any(re.search(kw, q_lower) for kw in keywords):
                complexity = level
                break

        # 判断类型
        if any(w in q_lower for w in ["如何", "怎么", "步骤", "how to", "steps"]):
            qtype = "procedural"
        elif any(w in q_lower for w in ["为什么", "原因", "why", "cause"]):
            qtype = "contextual"
        elif any(w in q_lower for w in ["列出", "所有", "全部", "list", "all"]):
            qtype = "exploratory"
        else:
            qtype = "factual"

        # 提取实体（简单名词提取）
        words = re.findall(r'\b[A-Z][a-z]+\b|[\u4e00-\u9fa5]{2,4}', query)
        entities = list(set(words))[:5]

        # 拆解子查询
        sub_queries = self._split_sub_queries(query, qtype, complexity, context)

        return QueryIntent(
            type=qtype,
            complexity=complexity,
            entities=entities,
            sub_queries=sub_queries
        )

    def _split_sub_queries(
        self,
        query: str,
        qtype: str,
        complexity: str,
        context: Dict[str, Any] = None
    ) -> List[str]:
        """拆解子查询"""
        queries = [query]

        if complexity == "simple":
            return queries

        # multi-hop：补充上下文相关查询
        if context:
            goal = context.get("active_goal", "")
            if goal and goal.lower() not in query.lower():
                queries.append(f"{goal} {query}")

        # deep：补充背景和历史查询
        if complexity == "deep":
            queries.append(f"{query} 背景")
            queries.append(f"{query} 历史记录")

        return queries[:4]


# ============================================================================
# Gap Analyzer（缺口分析）
# ============================================================================

class GapAnalyzer:
    """
    分析当前检索结果的缺口：
    - 哪些实体没被覆盖
    - 置信度是否足够
    - 是否需要继续检索
    """

    def analyze(
        self,
        query: str,
        intent: QueryIntent,
        memories: List[MemoryObject],
        step: int
    ) -> Tuple[List[str], float, bool]:
        """
        返回：(缺口列表, 置信度, 是否需要继续检索)
        """
        if not memories:
            return [f"未找到与 '{query}' 相关的记忆"], 0.0, step < 2

        # 检查实体覆盖率
        covered_entities = []
        all_text = " ".join(m.content + m.title for m in memories).lower()
        for entity in intent.entities:
            if entity.lower() in all_text:
                covered_entities.append(entity)

        uncovered = [e for e in intent.entities if e not in covered_entities]

        # 计算置信度
        avg_weight = sum(m.temporal_weight for m in memories) / len(memories)
        entity_coverage = len(covered_entities) / max(len(intent.entities), 1)
        confidence = (avg_weight * 0.4 + entity_coverage * 0.4 + min(len(memories) / 5, 1) * 0.2)

        # 是否继续
        need_more = (
            step < 2 and                      # 最多 3 步
            confidence < 0.7 and              # 置信度不足
            intent.complexity != "simple"     # 简单查询不追加
        )

        gaps = [f"实体未覆盖: {e}" for e in uncovered]
        if confidence < 0.5:
            gaps.append(f"整体相关性偏低 (confidence={confidence:.2f})")

        return gaps, round(confidence, 3), need_more


# ============================================================================
# Query Rewriter（查询改写）
# ============================================================================

class QueryRewriter:
    """
    基于上一步的结果和缺口，改写查询。
    """

    def rewrite(
        self,
        original_query: str,
        gaps: List[str],
        previous_memories: List[MemoryObject],
        step: int
    ) -> List[str]:
        """生成改写后的查询列表"""
        rewrites = []

        # 从已有记忆里提取关联词
        if previous_memories:
            # 取最相关记忆的 runtime_dependencies 作为扩展
            top_mem = previous_memories[0]
            for dep in top_mem.runtime_dependencies[:2]:
                rewrites.append(f"{original_query} {dep}")

            # 用 linked_memories 的标题扩展（如果能查到）
            if top_mem.tags:
                tag_query = " ".join(top_mem.tags[:2])
                rewrites.append(f"{tag_query} {original_query}")

        # 针对实体缺口改写
        for gap in gaps:
            if "实体未覆盖" in gap:
                entity = gap.replace("实体未覆盖: ", "")
                rewrites.append(f"{entity} {original_query}")

        # 深度改写：换角度
        if step == 1:
            rewrites.append(f"{original_query} 详细")
            rewrites.append(f"{original_query} 历史")

        # 去重，限制数量
        seen = set([original_query])
        result = []
        for q in rewrites:
            q = q.strip()
            if q and q not in seen:
                seen.add(q)
                result.append(q)

        return result[:3]


# ============================================================================
# Memory Stitcher（记忆拼接）
# ============================================================================

class MemoryStitcher:
    """
    把多步检索结果拼接、去重、按综合分排序。
    """

    def stitch(
        self,
        steps: List[RetrievalStep],
        intent: QueryIntent
    ) -> Tuple[List[MemoryObject], Dict[str, float]]:
        """
        返回：(拼接后的记忆列表, relevance_scores)
        """
        seen_ids = set()
        scored: Dict[str, Tuple[MemoryObject, float]] = {}

        for step_result in steps:
            # 越早步骤的结果权重越高（第1步最相关）
            step_weight = 1.0 / (step_result.step + 1)

            for rank, mem in enumerate(step_result.memories):
                if mem.id in scored:
                    # 多步都召回同一条记忆，提升分数
                    existing_score = scored[mem.id][1]
                    scored[mem.id] = (mem, min(1.0, existing_score + 0.2 * step_weight))
                else:
                    # 排名越靠前分越高
                    rank_score = 1.0 / (rank + 1)
                    base_score = rank_score * step_weight * step_result.confidence
                    scored[mem.id] = (mem, round(base_score, 3))

        # 按分排序
        sorted_items = sorted(scored.values(), key=lambda x: x[1], reverse=True)

        memories = [item[0] for item in sorted_items]
        scores = {item[0].id: item[1] for item in sorted_items}

        return memories, scores


# ============================================================================
# JIT Memory Compiler（主类）
# ============================================================================

class JITMemoryCompiler:
    """
    即时记忆编译器

    用法：
        compiler = JITMemoryCompiler(semantic_engine)
        result = compiler.compile(query="调度器故障原因", context=runtime_context)
        # result.memories → 多步检索后的最终记忆列表
        # result.query_trace → ["原始查询", "改写1", "改写2"]
        # result.steps → 每步详情
    """

    def __init__(self, semantic_engine, temporal_engine=None):
        self.semantic_engine = semantic_engine
        self.temporal_engine = temporal_engine
        self.analyzer = QueryAnalyzer()
        self.gap_analyzer = GapAnalyzer()
        self.rewriter = QueryRewriter()
        self.stitcher = MemoryStitcher()

    def compile(
        self,
        query: str,
        context: Dict[str, Any] = None,
        top_k: int = 5,
        max_steps: int = 3
    ) -> CompileResult:
        """
        执行 JIT 多步推理检索

        Args:
            query: 用户查询
            context: 当前运行时上下文
            top_k: 最终返回的记忆数量
            max_steps: 最大检索步数（默认 3）
        """
        context = context or {}
        steps: List[RetrievalStep] = []
        query_trace: List[str] = [query]

        # Step 1: 分析意图
        intent = self.analyzer.analyze(query, context)

        # Step 2: 执行多步检索
        current_queries = intent.sub_queries if intent.sub_queries else [query]
        current_step = 0

        while current_step < max_steps:
            # 当前步的所有查询合并检索
            step_memories: List[MemoryObject] = []
            seen_ids = set()

            for q in current_queries:
                try:
                    results = self.semantic_engine.retrieve(
                        query=q,
                        top_k=top_k
                    )
                    for mem in results:
                        if mem.id not in seen_ids:
                            seen_ids.add(mem.id)
                            step_memories.append(mem)
                except Exception as e:
                    print(f"[JIT] 检索失败 ({q}): {e}")

            # 分析缺口
            gaps, confidence, need_more = self.gap_analyzer.analyze(
                query=query,
                intent=intent,
                memories=step_memories,
                step=current_step
            )

            # 记录本步
            step_result = RetrievalStep(
                step=current_step,
                query=" | ".join(current_queries),
                memories=step_memories,
                gaps=gaps,
                confidence=confidence
            )
            steps.append(step_result)

            # 不需要继续或已到最大步数
            if not need_more or current_step >= max_steps - 1:
                break

            # 改写查询，准备下一步
            rewrites = self.rewriter.rewrite(
                original_query=query,
                gaps=gaps,
                previous_memories=step_memories,
                step=current_step
            )

            if not rewrites:
                break

            current_queries = rewrites
            query_trace.extend(rewrites)
            current_step += 1

        # Step 3: 拼接所有步骤结果
        final_memories, scores = self.stitcher.stitch(steps, intent)
        final_memories = final_memories[:top_k]

        # Step 4: 强化被召回的记忆（SM-2）
        if self.temporal_engine:
            for mem in final_memories:
                try:
                    self.temporal_engine.reinforce_access(mem.id, grade=0.75)
                except Exception:
                    pass

        # 生成说明
        total_candidates = sum(len(s.memories) for s in steps)
        reasoning = (
            f"JIT编译 {len(steps)} 步，"
            f"查询轨迹：{' → '.join(query_trace[:4])}{'...' if len(query_trace) > 4 else ''}，"
            f"候选 {total_candidates} 条 → 精选 {len(final_memories)} 条"
            f"（置信度：{steps[-1].confidence:.2f}）"
        )

        return CompileResult(
            memories=final_memories,
            steps=steps,
            query_trace=query_trace,
            relevance_scores=scores,
            reasoning=reasoning
        )
