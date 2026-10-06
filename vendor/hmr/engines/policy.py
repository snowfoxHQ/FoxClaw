"""
Memory Policy Engine - 记忆策略引擎
HMR v2.0 新增

解决的问题：
  v1.x 的存/取/忘策略全部硬编码，无法从经验中学习
  PolicyEngine 让系统从实际使用反馈中学习最优策略：
    - IngestPolicy：什么值得存？存成什么类型？置信度多少？
    - RecallPolicy：什么时候用 JIT？top_k 设多少？
    - ForgetPolicy：什么时候降权？什么时候删除？

学习机制：
  使用带动量的梯度更新（类 SGD），无需 GPU/大数据
  反馈信号：
    - recall_hit：召回结果被实际使用 → 正向奖励
    - recall_miss：召回结果无用 → 负向惩罚
    - compress_gain：压缩后召回质量上升 → 正向
    - task_success：任务完成 → 关联记忆正向
    - task_failure：任务失败 → 关联记忆负向
"""

import json
import re
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
from collections import deque


# ============================================================================
# 反馈事件
# ============================================================================

@dataclass
class FeedbackEvent:
    """一次策略反馈事件"""
    event_type: str        # recall_hit / recall_miss / compress_gain / task_success / task_failure
    memory_ids: List[str]  # 相关的记忆 ID
    signal: float          # 强度 -1.0~1.0（正=好 负=坏）
    context: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "memory_ids": self.memory_ids,
            "signal": self.signal,
            "context": self.context,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FeedbackEvent":
        d = d.copy()
        d["timestamp"] = datetime.fromisoformat(d["timestamp"])
        return cls(**d)


# ============================================================================
# Ingest Policy（存储策略）
# ============================================================================

class IngestPolicy:
    """
    决定：什么值得存，存成什么类型，置信度设多少。

    特征向量（12维）：
      [0] 内容长度（归一化）
      [1] 包含数字/指标
      [2] 包含失败关键词
      [3] 包含成功关键词
      [4] 包含决策关键词
      [5] 包含原因关键词
      [6] 已有相关记忆数量（归一化）
      [7] 当前时间权重（运行时活跃度）
      [8] tag 数量
      [9] runtime_dependencies 数量
      [10] 内容重复度（与已有摘要的词重叠）
      [11] 标题质量（有标题=1，无=0）
    """

    TYPE_LABELS = ["concept", "decision", "execution", "reflection", "task"]
    DEFAULT_WEIGHTS = {
        # 特征索引: [长度, 数字, 失败词, 成功词, 决策词, 原因词, 已有记忆数, 活跃度, tag数, dep数, 重复度, 有标题, 概念词]
        "concept":    [ 0.0, -0.1,  -0.2,  0.1,  -0.1,  0.3, -0.1,  0.0,  0.2,  0.1, -0.3,  0.0,  0.6],
        "decision":   [-0.1,  0.0,  -0.2,  0.2,   0.6,  0.1,  0.0,  0.0,  0.1,  0.3, -0.2,  0.1, -0.3],
        "execution":  [ 0.3,  0.4,   0.7,  0.2,  -0.1,  0.1,  0.0,  0.1,  0.0,  0.0, -0.2,  0.0, -0.3],
        "reflection": [ 0.1,  0.0,   0.3,  0.0,   0.0,  0.5,  0.1,  0.0,  0.1,  0.0, -0.1,  0.0, -0.3],
        "task":       [ 0.1,  0.0,  -0.3, -0.1,   0.2, -0.1,  0.0,  0.0,  0.0,  0.2, -0.1,  0.1, -0.3],
    }
    IMPORTANCE_WEIGHTS = [0.1, 0.15, 0.35, 0.1, 0.15, 0.2, 0.0, 0.05, 0.1, 0.1, -0.35, 0.05, 0.1]

    # 关键词集合
    FAIL_WORDS  = {
        "失败","错误","死锁","超时","崩溃","积压","故障","异常","不足","问题","缺陷","阻塞","中断",
        "fail","error","crash","timeout","bug","wrong","issue","broken","blocked","overflow"
    }
    SUCCESS_WORDS = {
        "成功","完成","通过","正常","优化","降低","提升","改善","恢复","解决",
        "success","done","pass","improved","fixed","resolved","optimised","reduced"
    }
    DECISION_WORDS = {
        # 只保留"已做决策"的词，"建议/推荐"是泛化概念词，不放这里
        "决定","选择","采用","决策","选用","确定","批准",
        "decided","chose","selected","adopted","determined","approved"
    }
    CONCEPT_WORDS = {
        "建议","推荐","模式","原则","方式","架构","设计","规范","最佳实践","理念",
        "recommend","pattern","principle","approach","design","architecture","best practice"
    }
    REASON_WORDS   = {
        "因为","由于","原因","导致","根因","分析","推断","基于","根据",
        "because","cause","reason","due to","resulted","analysis","therefore","hence"
    }

    def __init__(self):
        self.weights = {k: list(v) for k, v in self.DEFAULT_WEIGHTS.items()}
        self.importance_weights = list(self.IMPORTANCE_WEIGHTS)
        self._lr = 0.01   # 学习率
        self._momentum = {k: [0.0]*13 for k in self.TYPE_LABELS}

    def should_store(self, content: str, context: Dict[str, Any]) -> bool:
        """判断这条内容是否值得存入长期记忆"""
        importance = self._compute_importance(content, context)
        # 基准阈值 0.3，重复内容不存
        return importance >= 0.3

    def classify_type(self, content: str, context: Dict[str, Any]) -> str:
        """推断最适合的 memory_type"""
        features = self._extract_features(content, context)
        scores = {}
        for mtype, w in self.weights.items():
            score = sum(f * wi for f, wi in zip(features, w))
            scores[mtype] = score
        return max(scores, key=lambda k: scores[k])

    def recommend_confidence(self, content: str, context: Dict[str, Any]) -> float:
        """推荐置信度"""
        importance = self._compute_importance(content, context)
        # 置信度 = 重要性 * 0.6 + 0.4 基线
        return round(min(1.0, importance * 0.6 + 0.4), 2)

    def update(self, memory_id: str, feedback: float, features: List[float], mtype: str):
        """基于反馈更新权重（带动量的 SGD）"""
        if mtype not in self.weights:
            return
        w = self.weights[mtype]
        m = self._momentum[mtype]
        for i in range(min(len(features), len(w))):
            grad = -feedback * features[i]
            m[i] = 0.9 * m[i] + self._lr * grad
            w[i] = max(-1.0, min(1.0, w[i] - m[i]))

    def _extract_features(self, content: str, context: Dict[str, Any]) -> List[float]:
        """
        中英文兼容的特征提取：
        使用字符级 in 检查（不依赖空格分词），同时兼容中文和英文。
        """
        text = content.lower()
        # 字符级关键词匹配（对中文无需分词）
        fail_score    = min(1.0, sum(1 for w in self.FAIL_WORDS    if w in text) / 3)
        success_score = min(1.0, sum(1 for w in self.SUCCESS_WORDS  if w in text) / 3)
        decision_score= min(1.0, sum(1 for w in self.DECISION_WORDS if w in text) / 2)
        reason_score  = min(1.0, sum(1 for w in self.REASON_WORDS   if w in text) / 2)
        concept_score = min(1.0, sum(1 for w in self.CONCEPT_WORDS if w in text) / 2)
        return [
            min(1.0, len(content) / 500),
            1.0 if re.search(r'\d+', content) else 0.0,
            fail_score,
            success_score,
            decision_score,
            reason_score,
            min(1.0, context.get("existing_memory_count", 0) / 100),
            context.get("runtime_activity", 0.5),
            min(1.0, len(context.get("tags", [])) / 5),
            min(1.0, len(context.get("runtime_dependencies", [])) / 5),
            context.get("content_overlap", 0.0),
            1.0 if context.get("title") else 0.0,
            concept_score,   # ← 新增第13维：概念词得分
        ]

    def _compute_importance(self, content: str, context: Dict[str, Any]) -> float:
        features = self._extract_features(content, context)
        score = sum(f * w for f, w in zip(features, self.importance_weights))
        return max(0.0, min(1.0, score + 0.5))

    def to_dict(self) -> dict:
        return {"weights": self.weights, "importance_weights": self.importance_weights}

    @classmethod
    def from_dict(cls, d: dict) -> "IngestPolicy":
        p = cls()
        p.weights = d.get("weights", p.weights)
        p.importance_weights = d.get("importance_weights", p.importance_weights)
        return p


# ============================================================================
# Recall Policy（召回策略）
# ============================================================================

class RecallPolicy:
    """
    决定：用哪种策略召回，top_k 设多少，是否走 JIT。

    特征向量（8维）：
      [0] 查询长度（归一化）
      [1] 包含推理词（why/cause/因为）
      [2] 包含时间词（recent/last/最近）
      [3] 当前任务复杂度
      [4] 历史召回命中率
      [5] 当前记忆库大小（归一化）
      [6] 是否有活跃 goal
      [7] 上次召回失败标记
    """

    STRATEGY_LABELS = ["semantic", "temporal", "jit", "hybrid", "graph"]
    DEFAULT_STRATEGY_WEIGHTS = {
        "semantic": [ 0.0,  0.0,  0.0,  0.0,  0.2,  0.0,  0.0,  0.0],
        "temporal": [ 0.0, -0.2,  0.5,  0.0,  0.0,  0.0,  0.0,  0.0],
        "jit":      [ 0.2,  0.5, -0.1,  0.3,  0.0,  0.1,  0.0,  0.3],
        "hybrid":   [ 0.1,  0.2,  0.0,  0.2,  0.1,  0.0,  0.5,  0.1],
        "graph":    [ 0.0,  0.3,  0.0,  0.3,  0.0,  0.2,  0.1,  0.2],
    }
    TOP_K_WEIGHTS = [0.1, 0.3, 0.0, 0.4, -0.2, 0.2, 0.1, 0.2]  # → 调整 top_k

    REASON_WORDS = {"为什么","原因","因为","why","cause","reason","because","导致"}
    TIME_WORDS   = {"最近","上次","昨天","recent","last","yesterday","previously","before"}

    def __init__(self):
        self.strategy_weights = {k: list(v) for k, v in self.DEFAULT_STRATEGY_WEIGHTS.items()}
        self.top_k_weights = list(self.TOP_K_WEIGHTS)
        self._lr = 0.005
        self._hit_rate_history: deque = deque(maxlen=50)

    def select_strategy(self, query: str, context: Dict[str, Any]) -> str:
        features = self._extract_features(query, context)
        scores = {}
        for strat, w in self.strategy_weights.items():
            scores[strat] = sum(f * wi for f, wi in zip(features, w))
        return max(scores, key=lambda k: scores[k])

    def decide_top_k(self, query: str, context: Dict[str, Any], base: int = 5) -> int:
        features = self._extract_features(query, context)
        delta = sum(f * w for f, w in zip(features, self.top_k_weights))
        return max(3, min(12, base + int(delta * 5)))

    def record_hit(self, used: bool):
        self._hit_rate_history.append(1.0 if used else 0.0)

    def get_hit_rate(self) -> float:
        if not self._hit_rate_history:
            return 0.5
        return sum(self._hit_rate_history) / len(self._hit_rate_history)

    def update(self, strategy: str, feedback: float, features: List[float]):
        if strategy not in self.strategy_weights:
            return
        w = self.strategy_weights[strategy]
        for i in range(min(len(features), len(w))):
            grad = -feedback * features[i]
            w[i] = max(-1.0, min(1.0, w[i] - self._lr * grad))

    def _extract_features(self, query: str, context: Dict[str, Any]) -> List[float]:
        q_lower = (query or "").lower()
        # 字符级匹配（兼容中英文）
        reason_score = min(1.0, sum(1 for w in self.REASON_WORDS if w in q_lower) / 2)
        time_score   = min(1.0, sum(1 for w in self.TIME_WORDS   if w in q_lower) / 2)
        return [
            min(1.0, len(q_lower) / 100),
            reason_score,
            time_score,
            context.get("task_complexity", 0.5),
            self.get_hit_rate(),
            min(1.0, context.get("memory_count", 0) / 500),
            1.0 if context.get("active_goal") else 0.0,
            1.0 if context.get("last_recall_failed") else 0.0,
        ]

    def to_dict(self) -> dict:
        return {"strategy_weights": self.strategy_weights, "top_k_weights": self.top_k_weights}

    @classmethod
    def from_dict(cls, d: dict) -> "RecallPolicy":
        p = cls()
        p.strategy_weights = d.get("strategy_weights", p.strategy_weights)
        p.top_k_weights = d.get("top_k_weights", p.top_k_weights)
        return p


# ============================================================================
# Forget Policy（遗忘策略）
# ============================================================================

class ForgetPolicy:
    """
    决定：记忆应该以多快的速度衰减，什么时候应该删除。

    对不同类型的记忆采用不同的衰减速率：
      - insight / concept：慢衰减（知识价值持久）
      - execution：中等衰减（经验记录有时效性）
      - task：快衰减（完成后快速过期）
    """

    BASE_DECAY_RATES: Dict[str, float] = {
        "concept":     0.02,
        "decision":    0.02,
        "insight":     0.01,
        "reflection":  0.02,
        "project":     0.01,
        "execution":   0.04,
        "agent_memory":0.03,
        "task":        0.06,
        "workflow":    0.02,
    }
    # 反馈修正：召回命中的记忆衰减更慢
    HIT_DECAY_BONUS  =  0.005   # 每次命中，衰减率 -0.005（更慢）
    MISS_DECAY_BONUS = -0.003   # 每次未命中，衰减率 +0.003（更快）

    def __init__(self):
        self.decay_overrides: Dict[str, float] = {}  # memory_id → custom decay rate

    def get_decay_rate(self, memory) -> float:
        """获取记忆的个性化衰减率"""
        if memory.id in self.decay_overrides:
            return self.decay_overrides[memory.id]
        base = self.BASE_DECAY_RATES.get(memory.type, 0.03)
        # 置信度高的记忆衰减更慢
        confidence_bonus = (memory.confidence - 0.5) * 0.01
        return max(0.005, base - confidence_bonus)

    def should_delete(self, memory, retrievability: float) -> bool:
        """是否应该删除这条记忆"""
        # concept/insight 类型除非极低才删
        if memory.type in ("concept", "insight", "decision", "reflection"):
            return retrievability < 0.02 and memory.access_count == 0
        return retrievability < 0.05 and memory.access_count == 0

    def update_from_feedback(self, memory_id: str, mtype: str, was_useful: bool):
        """根据使用情况调整个性化衰减率"""
        base = self.BASE_DECAY_RATES.get(mtype, 0.03)
        current = self.decay_overrides.get(memory_id, base)
        if was_useful:
            # 有用的记忆衰减更慢
            self.decay_overrides[memory_id] = max(0.005, current + self.HIT_DECAY_BONUS)
        else:
            # 无用的记忆衰减更快
            self.decay_overrides[memory_id] = min(0.15, current - self.MISS_DECAY_BONUS)

    def to_dict(self) -> dict:
        return {"decay_overrides": self.decay_overrides}

    @classmethod
    def from_dict(cls, d: dict) -> "ForgetPolicy":
        p = cls()
        p.decay_overrides = d.get("decay_overrides", {})
        return p


# ============================================================================
# Memory Policy Engine（主类）
# ============================================================================

class MemoryPolicyEngine:
    """
    记忆策略引擎（三个子策略的协调者）

    使用方式：
        policy = MemoryPolicyEngine("./hmr_data/policy")

        # 存储决策
        if policy.ingest.should_store(content, ctx):
            mtype = policy.ingest.classify_type(content, ctx)
            conf  = policy.ingest.recommend_confidence(content, ctx)
            hmr.ingest(content, memory_type=mtype, metadata={"confidence": conf})

        # 召回决策
        strategy = policy.recall.select_strategy(query, ctx)
        top_k    = policy.recall.decide_top_k(query, ctx)
        result   = hmr.recall(query, strategy=strategy, top_k=top_k)

        # 遗忘决策
        decay_rate = policy.forget.get_decay_rate(memory)

        # 反馈（让策略从结果中学习）
        policy.record_feedback(
            event_type="recall_hit",
            memory_ids=[mem.id for mem in used_memories],
            signal=0.8
        )
    """

    def __init__(self, storage_path: Optional[str] = None):
        self.storage_path = Path(storage_path) if storage_path else None
        self._lock = threading.Lock()

        self.ingest = IngestPolicy()
        self.recall = RecallPolicy()
        self.forget = ForgetPolicy()

        # 反馈历史（最近 200 条）
        self._feedback_history: deque = deque(maxlen=200)

        # 统计
        self._stats = {
            "total_feedback": 0,
            "positive_feedback": 0,
            "negative_feedback": 0,
            "policy_updates": 0,
        }

        if self.storage_path:
            self.storage_path.mkdir(parents=True, exist_ok=True)
            self._load()

    # ── 反馈接口 ───────────────────────────────────────────────────────────

    def record_feedback(
        self,
        event_type: str,
        memory_ids: List[str],
        signal: float,
        context: Optional[Dict[str, Any]] = None,
        query: Optional[str] = None,
        strategy: Optional[str] = None,
    ):
        """
        记录一次策略反馈，并更新权重。

        event_type：
            recall_hit     - 召回结果被实际使用（signal > 0）
            recall_miss    - 召回结果完全没用到（signal < 0）
            compress_gain  - 压缩后召回质量提升（signal > 0）
            task_success   - 完整任务成功（signal = 0.5~1.0）
            task_failure   - 任务失败（signal = -0.5~-1.0）
        """
        event = FeedbackEvent(
            event_type=event_type,
            memory_ids=memory_ids,
            signal=signal,
            context=context or {}
        )

        with self._lock:
            self._feedback_history.append(event)
            self._stats["total_feedback"] += 1
            if signal > 0:
                self._stats["positive_feedback"] += 1
            else:
                self._stats["negative_feedback"] += 1

        # 更新各子策略的权重
        self._update_policies(event, query, strategy)

        # 每次有反馈都保存（确保重启后数据不丢失）
        if self.storage_path:
            self._save()

    def _update_policies(
        self, event: FeedbackEvent,
        query: Optional[str], strategy: Optional[str]
    ):
        ctx = event.context
        signal = event.signal

        # 更新召回策略
        if event.event_type in ("recall_hit", "recall_miss") and query and strategy:
            features = self.recall._extract_features(query, ctx)
            self.recall.update(strategy, signal, features)
            self.recall.record_hit(signal > 0)
            # 更新遗忘策略
            for mid in event.memory_ids:
                self.forget.update_from_feedback(mid, ctx.get("memory_type", "concept"), signal > 0)

        # 任务成功/失败 → 更新存储策略
        if event.event_type in ("task_success", "task_failure"):
            for mid in event.memory_ids:
                mtype = ctx.get("memory_type", "concept")
                content = ctx.get("content", "")
                if content:
                    features = self.ingest._extract_features(content, ctx)
                    self.ingest.update(mid, signal, features, mtype)
                self.forget.update_from_feedback(mid, mtype, signal > 0)

        self._stats["policy_updates"] += 1

    # ── 便捷方法（完整存储决策流程）─────────────────────────────────────────

    def decide_ingest(
        self,
        content: str,
        title: Optional[str] = None,
        tags: Optional[List[str]] = None,
        runtime_dependencies: Optional[List[str]] = None,
        existing_memory_count: int = 0,
    ) -> Dict[str, Any]:
        """
        返回完整的存储决策：是否存、用什么类型、置信度多少

        返回：
            {
                "should_store": bool,
                "memory_type": str,
                "confidence": float,
                "reasoning": str
            }
        """
        ctx = {
            "title": title,
            "tags": tags or [],
            "runtime_dependencies": runtime_dependencies or [],
            "existing_memory_count": existing_memory_count,
            "runtime_activity": 0.7,
            "content_overlap": 0.0,
        }

        should = self.ingest.should_store(content, ctx)
        mtype = self.ingest.classify_type(content, ctx)
        conf = self.ingest.recommend_confidence(content, ctx)

        type_reasons = {
            "execution": "包含失败/结果记录",
            "decision": "包含决策关键词",
            "reflection": "包含原因分析",
            "concept": "抽象知识内容",
            "task": "任务描述",
        }

        return {
            "should_store": should,
            "memory_type": mtype,
            "confidence": conf,
            "reasoning": f"{'值得存储' if should else '重复或价值低，跳过'} | "
                         f"分类={mtype}（{type_reasons.get(mtype, '')}）| "
                         f"置信度={conf}"
        }

    def decide_recall(
        self,
        query: str,
        context: Dict[str, Any],
        base_top_k: int = 5
    ) -> Dict[str, Any]:
        """
        返回完整的召回决策：策略和 top_k
        """
        ctx = dict(context)
        ctx["memory_count"] = ctx.get("total_memories", 0)
        ctx["task_complexity"] = min(1.0, len(ctx.get("pending_tasks", [])) / 5)

        strategy = self.recall.select_strategy(query, ctx)
        top_k = self.recall.decide_top_k(query, ctx, base_top_k)
        hit_rate = self.recall.get_hit_rate()

        return {
            "strategy": strategy,
            "top_k": top_k,
            "hit_rate": round(hit_rate, 3),
            "reasoning": f"策略={strategy}，top_k={top_k}，历史命中率={hit_rate:.0%}"
        }

    # ── 统计和持久化 ───────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        positive_ratio = (
            self._stats["positive_feedback"] /
            max(self._stats["total_feedback"], 1)
        )
        return {
            **self._stats,
            "positive_ratio": round(positive_ratio, 3),
            "recall_hit_rate": round(self.recall.get_hit_rate(), 3),
            "feedback_history_size": len(self._feedback_history),
            "custom_decay_rules": len(self.forget.decay_overrides),
        }

    def get_policy_weights_summary(self) -> Dict[str, Any]:
        """返回当前策略权重（用于调试和可视化）"""
        return {
            "ingest_type_bias": {
                mtype: round(sum(w) / len(w), 3)
                for mtype, w in self.ingest.weights.items()
            },
            "recall_strategy_bias": {
                strat: round(sum(w) / len(w), 3)
                for strat, w in self.recall.strategy_weights.items()
            },
            "forget_custom_rules": len(self.forget.decay_overrides),
        }

    def _save(self):
        if not self.storage_path:
            return
        data = {
            "ingest": self.ingest.to_dict(),
            "recall": self.recall.to_dict(),
            "forget": self.forget.to_dict(),
            "stats": self._stats,
            "feedback_history": [e.to_dict() for e in self._feedback_history],
            "saved_at": datetime.utcnow().isoformat(),
        }
        tmp = self.storage_path / "policy.tmp"
        target = self.storage_path / "policy.json"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        tmp.replace(target)

    def _load(self):
        target = self.storage_path / "policy.json"
        if not target.exists():
            return
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.ingest = IngestPolicy.from_dict(data.get("ingest", {}))
            self.recall = RecallPolicy.from_dict(data.get("recall", {}))
            self.forget = ForgetPolicy.from_dict(data.get("forget", {}))
            self._stats = data.get("stats", self._stats)
            for ev in data.get("feedback_history", []):
                self._feedback_history.append(FeedbackEvent.from_dict(ev))
            print(f"[PolicyEngine] 加载策略，历史反馈 {len(self._feedback_history)} 条")
        except Exception as e:
            print(f"[PolicyEngine] 策略加载失败: {e}")
