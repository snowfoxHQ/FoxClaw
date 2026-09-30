"""
ThoughtChain Engine - 显式推理链引擎
HMR v2.0 新增

解决的问题：
  v1.x 只存"发生了什么"（execution/decision），但不知道"为什么这样想"
  ThoughtChain 保存完整的思维过程，让 AI 能：
    1. 回溯推理路径（我当时为什么这样决策？）
    2. 反思错误（预期 vs 实际，找出错误思维节点）
    3. 传递认知链（接手任务时直接继承完整推理过程）

思维节点类型：
  observation  → 观察到的事实
  hypothesis   → 提出的假设
  decision     → 做出的决策
  action       → 执行的行动
  outcome      → 行动的结果
  reflection   → 事后反思
  insight      → 提炼的洞察

链的生命周期：
  open → active → reflecting → closed
"""

import json
import uuid
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


# ============================================================================
# 数据结构
# ============================================================================

class ThoughtType(Enum):
    OBSERVATION = "observation"    # 观察：发现了什么事实
    HYPOTHESIS  = "hypothesis"     # 假设：可能的原因或方案
    DECISION    = "decision"       # 决策：选择了哪个方案
    ACTION      = "action"         # 行动：具体执行了什么
    OUTCOME     = "outcome"        # 结果：行动产生了什么结果
    REFLECTION  = "reflection"     # 反思：事后回顾和评价
    INSIGHT     = "insight"        # 洞察：提炼的普适知识


class ChainStatus(Enum):
    OPEN       = "open"       # 刚创建，正在思考中
    ACTIVE     = "active"     # 已执行行动，等待结果
    REFLECTING = "reflecting" # 收到结果，正在反思
    CLOSED     = "closed"     # 已完成，产生洞察


@dataclass
class Thought:
    """单个思维节点"""
    thought_id: str
    chain_id: str
    thought_type: ThoughtType
    content: str

    # 关联
    parent_id: Optional[str] = None      # 基于哪个思维得出
    memory_ids: List[str] = field(default_factory=list)  # 依据的记忆

    # 质量指标
    confidence: float = 0.7              # 确信度 0-1
    was_correct: Optional[bool] = None   # 事后评估：是否正确（None=未评估）
    correction: Optional[str] = None     # 如果错了，正确答案是什么

    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "thought_id": self.thought_id,
            "chain_id": self.chain_id,
            "thought_type": self.thought_type.value,
            "content": self.content,
            "parent_id": self.parent_id,
            "memory_ids": self.memory_ids,
            "confidence": self.confidence,
            "was_correct": self.was_correct,
            "correction": self.correction,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Thought":
        d = d.copy()
        d["thought_type"] = ThoughtType(d["thought_type"])
        d["timestamp"] = datetime.fromisoformat(d["timestamp"])
        return cls(**d)


@dataclass
class ReflectionResult:
    """反思结果：评估整条链的质量"""
    chain_id: str
    accuracy: float                      # 整体准确率（正确思维/总思维）
    correct_nodes: List[str]             # 正确的思维 ID
    wrong_nodes: List[str]               # 错误的思维 ID
    key_mistakes: List[str]              # 主要错误描述
    insights: List[str]                  # 提炼的洞察
    suggested_memory: Optional[str]      # 建议存储的关键学习
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ThoughtChain:
    """完整的思维链"""
    chain_id: str
    goal: str
    status: ChainStatus = ChainStatus.OPEN

    thoughts: List[Thought] = field(default_factory=list)

    # 结果
    expected_outcome: Optional[str] = None
    actual_outcome: Optional[str] = None
    reflection: Optional[ReflectionResult] = None

    # 元数据
    agent_id: Optional[str] = None
    runtime_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None

    # ── 便利方法 ──────────────────────────────────────────────────────────

    def add_thought(self, thought: Thought):
        self.thoughts.append(thought)

    def get_latest(self, n: int = 1) -> List[Thought]:
        return self.thoughts[-n:]

    def get_by_type(self, ttype: ThoughtType) -> List[Thought]:
        return [t for t in self.thoughts if t.thought_type == ttype]

    def get_decision_chain(self) -> List[Thought]:
        """返回从第一个 observation 到最后一个 decision 的主干路径"""
        key_types = {ThoughtType.OBSERVATION, ThoughtType.HYPOTHESIS,
                     ThoughtType.DECISION, ThoughtType.ACTION}
        return [t for t in self.thoughts if t.thought_type in key_types]

    def to_summary(self) -> str:
        """生成人类可读的链摘要"""
        lines = [f"目标: {self.goal}", f"状态: {self.status.value}", ""]
        for t in self.thoughts:
            icon = {
                "observation": "👁", "hypothesis": "💭", "decision": "✅",
                "action": "⚡", "outcome": "📊", "reflection": "🔍", "insight": "💡"
            }.get(t.thought_type.value, "·")
            correct_marker = (
                " ✓" if t.was_correct is True else
                " ✗" if t.was_correct is False else ""
            )
            lines.append(f"  {icon} [{t.thought_type.value}]{correct_marker} {t.content[:60]}")
        if self.reflection:
            lines.append(f"\n反思准确率: {self.reflection.accuracy:.0%}")
            for ins in self.reflection.insights:
                lines.append(f"  💡 {ins}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "chain_id": self.chain_id,
            "goal": self.goal,
            "status": self.status.value,
            "thoughts": [t.to_dict() for t in self.thoughts],
            "expected_outcome": self.expected_outcome,
            "actual_outcome": self.actual_outcome,
            "reflection": self._reflection_to_dict(),
            "agent_id": self.agent_id,
            "runtime_id": self.runtime_id,
            "created_at": self.created_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
        }

    def _reflection_to_dict(self) -> Optional[dict]:
        if not self.reflection:
            return None
        r = self.reflection
        return {
            "chain_id": r.chain_id,
            "accuracy": r.accuracy,
            "correct_nodes": r.correct_nodes,
            "wrong_nodes": r.wrong_nodes,
            "key_mistakes": r.key_mistakes,
            "insights": r.insights,
            "suggested_memory": r.suggested_memory,
            "timestamp": r.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ThoughtChain":
        d = d.copy()
        d["status"] = ChainStatus(d["status"])
        d["thoughts"] = [Thought.from_dict(t) for t in d.get("thoughts", [])]
        d["created_at"] = datetime.fromisoformat(d["created_at"])
        if d.get("closed_at"):
            d["closed_at"] = datetime.fromisoformat(d["closed_at"])

        raw_ref = d.pop("reflection", None)
        chain = cls(**d)
        if raw_ref:
            raw_ref["timestamp"] = datetime.fromisoformat(raw_ref["timestamp"])
            chain.reflection = ReflectionResult(**raw_ref)
        return chain


# ============================================================================
# Reflector（反思器）
# ============================================================================

class Reflector:
    """
    对一条思维链做事后反思：
    - 对比预期结果 vs 实际结果
    - 标记哪些思维节点是错误的
    - 提炼洞察
    - 建议存储哪些学习到长期记忆
    """

    def reflect(
        self,
        chain: ThoughtChain,
        actual_outcome: str,
        outcome_rating: float = None  # 0-1，人工评分（可选）
    ) -> ReflectionResult:
        """
        执行反思。

        Args:
            chain: 要反思的思维链
            actual_outcome: 实际发生了什么
            outcome_rating: 人工评分（0=完全失败, 1=完全成功），不传则自动推断
        """
        chain.actual_outcome = actual_outcome
        chain.status = ChainStatus.REFLECTING

        # 推断评分
        if outcome_rating is None:
            outcome_rating = self._infer_rating(chain.expected_outcome, actual_outcome)

        # 标记思维节点
        correct_nodes, wrong_nodes = self._evaluate_thoughts(chain, outcome_rating)

        # 找主要错误
        key_mistakes = self._identify_mistakes(chain, wrong_nodes)

        # 提炼洞察
        insights = self._extract_insights(chain, actual_outcome, outcome_rating)

        # 建议记忆
        suggested_memory = self._suggest_memory(chain, insights, outcome_rating)

        accuracy = len(correct_nodes) / max(len(chain.thoughts), 1)

        result = ReflectionResult(
            chain_id=chain.chain_id,
            accuracy=accuracy,
            correct_nodes=correct_nodes,
            wrong_nodes=wrong_nodes,
            key_mistakes=key_mistakes,
            insights=insights,
            suggested_memory=suggested_memory,
        )

        chain.reflection = result
        chain.status = ChainStatus.CLOSED
        chain.closed_at = datetime.utcnow()

        return result

    def _infer_rating(self, expected: Optional[str], actual: str) -> float:
        """推断结果评分（简单词级重叠）"""
        if not expected:
            # 无预期，中性评分
            neg_words = ["失败", "错误", "超时", "崩溃", "fail", "error", "crash", "timeout"]
            pos_words = ["成功", "完成", "正常", "通过", "success", "done", "ok", "pass"]
            actual_lower = actual.lower()
            neg_score = sum(1 for w in neg_words if w in actual_lower)
            pos_score = sum(1 for w in pos_words if w in actual_lower)
            if neg_score > pos_score:
                return 0.2
            elif pos_score > neg_score:
                return 0.9
            return 0.5

        # 有预期：计算词级重叠度
        exp_words = set(expected.lower().split())
        act_words = set(actual.lower().split())
        if not exp_words:
            return 0.5
        overlap = len(exp_words & act_words) / len(exp_words)
        return min(1.0, overlap * 1.5)

    def _evaluate_thoughts(
        self, chain: ThoughtChain, rating: float
    ) -> Tuple[List[str], List[str]]:
        """
        根据最终评分，倒推哪些决策/假设节点是错误的。

        规则：
        - 结果好（rating > 0.7）→ decisions 和 hypotheses 都标为正确
        - 结果差（rating < 0.4）→ 找最后的 decision，标为错误；hypothesis 有一个错误
        - 中等 → 随机保留部分正确
        """
        correct_ids = []
        wrong_ids = []

        for t in chain.thoughts:
            if t.thought_type in (ThoughtType.OBSERVATION, ThoughtType.OUTCOME):
                # 观察和结果节点本身不评判对错
                t.was_correct = None
                continue

            if rating >= 0.7:
                t.was_correct = True
                correct_ids.append(t.thought_id)
            elif rating <= 0.3:
                if t.thought_type in (ThoughtType.DECISION, ThoughtType.HYPOTHESIS):
                    t.was_correct = False
                    wrong_ids.append(t.thought_id)
                else:
                    t.was_correct = True
                    correct_ids.append(t.thought_id)
            else:
                # 中等结果：行动正确但决策可能有问题
                if t.thought_type == ThoughtType.DECISION:
                    t.was_correct = (rating > 0.5)
                    (correct_ids if t.was_correct else wrong_ids).append(t.thought_id)
                else:
                    t.was_correct = True
                    correct_ids.append(t.thought_id)

        return correct_ids, wrong_ids

    def _identify_mistakes(
        self, chain: ThoughtChain, wrong_ids: List[str]
    ) -> List[str]:
        mistakes = []
        for t in chain.thoughts:
            if t.thought_id in wrong_ids:
                mistakes.append(
                    f"[{t.thought_type.value}] 思维有误: {t.content[:50]}"
                )
        return mistakes

    def _extract_insights(
        self, chain: ThoughtChain, actual_outcome: str, rating: float
    ) -> List[str]:
        insights = []

        if rating >= 0.8:
            decisions = chain.get_by_type(ThoughtType.DECISION)
            if decisions:
                insights.append(
                    f"有效决策路径：{decisions[-1].content[:40]}（评分={rating:.0%}）"
                )
        elif rating <= 0.3:
            hypotheses = chain.get_by_type(ThoughtType.HYPOTHESIS)
            if hypotheses:
                insights.append(
                    f"假设方向有误：{hypotheses[0].content[:40]} → 实际:{actual_outcome[:30]}"
                )
            insights.append("下次应先验证假设，再做决策")

        # 通用洞察：基于目标和结果
        insights.append(
            f"目标「{chain.goal[:30]}」: "
            f"{'达成' if rating > 0.6 else '未达成'}（评分={rating:.0%}）"
        )

        return insights

    def _suggest_memory(
        self, chain: ThoughtChain, insights: List[str], rating: float
    ) -> Optional[str]:
        if not insights:
            return None

        # 只在有明确学习价值时建议存储
        if rating > 0.85 or rating < 0.25:
            decisions = chain.get_by_type(ThoughtType.DECISION)
            decision_str = decisions[-1].content[:80] if decisions else ""

            return (
                f"[思维链洞察] 目标:{chain.goal[:30]} | "
                f"结果:{'成功' if rating > 0.6 else '失败'} | "
                f"关键决策:{decision_str} | "
                f"洞察:{' / '.join(insights[:2])}"
            )
        return None


# ============================================================================
# ThoughtChain Engine（主类）
# ============================================================================

class ThoughtChainEngine:
    """
    思维链引擎

    核心功能：
    1. 创建思维链（一次推理任务的开始）
    2. 追加思维节点（推理过程记录）
    3. 触发反思（执行完毕后评估推理质量）
    4. 查询思维链（找到类似问题的历史推理）
    5. 持久化（重启后恢复推理历史）

    使用示例：
        engine = ThoughtChainEngine("./hmr_data/chains")

        # 开始推理任务
        chain = engine.create_chain("为什么调度器超时", agent_id="agent_01")

        # 记录推理过程
        engine.add_thought(chain.chain_id, "IPC 队列深度超过 1000", ThoughtType.OBSERVATION)
        engine.add_thought(chain.chain_id, "可能是生产者速度超过消费者", ThoughtType.HYPOTHESIS)
        engine.add_thought(chain.chain_id, "决定增加消费者线程数", ThoughtType.DECISION)
        engine.add_thought(chain.chain_id, "已部署消费者 x3", ThoughtType.ACTION)

        # 执行后触发反思
        result = engine.reflect(chain.chain_id, actual_outcome="延迟降低 80%", rating=0.9)
        print(result.insights)
    """

    def __init__(self, storage_path: Optional[str] = None):
        self.storage_path = Path(storage_path) if storage_path else None
        self._lock = threading.Lock()
        self.chains: Dict[str, ThoughtChain] = {}
        self.reflector = Reflector()
        self._ingest_fn = None  # 注入 HMR.ingest（存储洞察到长期记忆）

        if self.storage_path:
            self.storage_path.mkdir(parents=True, exist_ok=True)
            self._load_all()

    def register_ingest_fn(self, fn):
        """注入 HMR.ingest 函数，用于把洞察存入长期记忆"""
        self._ingest_fn = fn

    # ── 创建和管理 ─────────────────────────────────────────────────────────

    def create_chain(
        self,
        goal: str,
        agent_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
        expected_outcome: Optional[str] = None,
    ) -> ThoughtChain:
        chain = ThoughtChain(
            chain_id=f"tc_{uuid.uuid4().hex[:8]}",
            goal=goal,
            agent_id=agent_id,
            runtime_id=runtime_id,
            expected_outcome=expected_outcome,
        )
        with self._lock:
            self.chains[chain.chain_id] = chain
        self._save(chain)
        return chain

    def add_thought(
        self,
        chain_id: str,
        content: str,
        thought_type: ThoughtType,
        confidence: float = 0.7,
        memory_ids: Optional[List[str]] = None,
        parent_id: Optional[str] = None,
    ) -> Optional[Thought]:
        chain = self.chains.get(chain_id)
        if not chain or chain.status == ChainStatus.CLOSED:
            return None

        # 自动推断 parent_id（默认接在最后一个思维节点后）
        if parent_id is None and chain.thoughts:
            parent_id = chain.thoughts[-1].thought_id

        thought = Thought(
            thought_id=f"th_{uuid.uuid4().hex[:8]}",
            chain_id=chain_id,
            thought_type=thought_type,
            content=content,
            confidence=confidence,
            memory_ids=memory_ids or [],
            parent_id=parent_id,
        )

        with self._lock:
            chain.add_thought(thought)
            if thought_type == ThoughtType.ACTION:
                chain.status = ChainStatus.ACTIVE

        self._save(chain)
        return thought

    def reflect(
        self,
        chain_id: str,
        actual_outcome: str,
        outcome_rating: Optional[float] = None,
    ) -> Optional[ReflectionResult]:
        chain = self.chains.get(chain_id)
        if not chain:
            return None

        result = self.reflector.reflect(chain, actual_outcome, outcome_rating)
        self._save(chain)

        # 把高价值洞察存入长期记忆
        if result.suggested_memory and self._ingest_fn:
            try:
                self._ingest_fn(
                    content=result.suggested_memory,
                    memory_type="reflection",
                    title=f"[链洞察] {chain.goal[:30]}",
                    metadata={
                        "tags": ["thought_chain", "reflection", "insight"],
                        "confidence": result.accuracy,
                        "runtime_dependencies": [chain.goal]
                    }
                )
            except Exception as e:
                print(f"[ThoughtChain] 洞察存储失败: {e}")

        return result

    # ── 查询 ───────────────────────────────────────────────────────────────

    def get_chain(self, chain_id: str) -> Optional[ThoughtChain]:
        return self.chains.get(chain_id)

    def find_similar_chains(
        self, goal: str, top_k: int = 3
    ) -> List[ThoughtChain]:
        """找与当前目标相似的历史思维链（关键词匹配）"""
        goal_words = set(goal.lower().split())
        scored = []
        for chain in self.chains.values():
            if chain.status != ChainStatus.CLOSED:
                continue
            chain_words = set(chain.goal.lower().split())
            overlap = len(goal_words & chain_words) / max(len(goal_words), 1)
            if overlap > 0.3:
                scored.append((chain, overlap))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in scored[:top_k]]

    def get_active_chains(
        self, agent_id: Optional[str] = None
    ) -> List[ThoughtChain]:
        return [
            c for c in self.chains.values()
            if c.status != ChainStatus.CLOSED
            and (agent_id is None or c.agent_id == agent_id)
        ]

    def get_best_decision_for(self, goal: str) -> Optional[str]:
        """从历史成功链中找出对同类问题最好的决策"""
        similar = self.find_similar_chains(goal)
        best_chain = None
        best_accuracy = 0.0

        for chain in similar:
            if chain.reflection and chain.reflection.accuracy > best_accuracy:
                best_accuracy = chain.reflection.accuracy
                best_chain = chain

        if not best_chain or best_accuracy < 0.7:
            return None

        decisions = best_chain.get_by_type(ThoughtType.DECISION)
        correct_decisions = [d for d in decisions if d.was_correct]
        if correct_decisions:
            return (
                f"[历史参考 准确率={best_accuracy:.0%}] "
                f"{correct_decisions[-1].content}"
            )
        return None

    def get_stats(self) -> Dict[str, Any]:
        total = len(self.chains)
        by_status = {}
        total_accuracy = 0.0
        reflected_count = 0

        for chain in self.chains.values():
            s = chain.status.value
            by_status[s] = by_status.get(s, 0) + 1
            if chain.reflection:
                total_accuracy += chain.reflection.accuracy
                reflected_count += 1

        avg_accuracy = total_accuracy / reflected_count if reflected_count > 0 else 0.0

        return {
            "total_chains": total,
            "by_status": by_status,
            "reflected_chains": reflected_count,
            "avg_reflection_accuracy": round(avg_accuracy, 3),
            "active_chains": by_status.get("open", 0) + by_status.get("active", 0),
        }

    # ── 持久化 ─────────────────────────────────────────────────────────────

    def _save(self, chain: ThoughtChain):
        if not self.storage_path:
            return
        path = self.storage_path / f"{chain.chain_id}.json"
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(chain.to_dict(), f, ensure_ascii=False, indent=2, default=str)
        tmp.replace(path)

    def _load_all(self):
        if not self.storage_path:
            return
        count = 0
        for path in self.storage_path.glob("tc_*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                chain = ThoughtChain.from_dict(data)
                self.chains[chain.chain_id] = chain
                count += 1
            except Exception as e:
                print(f"[ThoughtChain] 加载失败 {path.name}: {e}")
        if count:
            print(f"[ThoughtChain] 加载 {count} 条思维链")
