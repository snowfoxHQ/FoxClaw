"""
认知调度器 (Cognitive Scheduler)

把任务调度建模成真正的 OS 调度:特征化 → 打分 → 负载调整 → 路由 + 抢占判定。
借鉴 Codex 版 Foxclaw 的 CognitiveScheduler 思想,适配本项目的五 Agent 架构。

主管在分诊前先过这一层,得到:优先级、执行模式(抢占/标准)、预算、调度理由。
让主管从"关键词分诊"升级为"特征感知 + 负载感知的 OS 式调度器"。
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class TaskIntent:
    """任务意图:粗粒度类别 + 三个亲和度提示。"""
    category: str                 # reasoning / memory / workflow / tool
    confidence: float
    memory_affinity: float        # 记忆依赖度
    reasoning_depth: float        # 推理深度提示


@dataclass
class TaskFeatures:
    complexity: float             # 复杂度
    memory_dependency: float      # 记忆依赖
    tool_cost: float              # 工具成本
    reasoning_depth: float        # 推理深度
    urgency: float                # 紧急度
    expected_runtime: float       # 预期耗时


@dataclass
class SystemContext:
    """系统负载上下文。主管据此调整优先级、决定是否抢占。"""
    active_agents: int = 0        # 当前活跃 Agent 数
    queue_depth: int = 0          # 待处理任务队列深度
    system_load: float = 0.1      # 系统负载 0-1


@dataclass
class ScheduleDecision:
    priority: float               # 最终优先级 0-1
    route_hint: str               # 路由倾向:agent / tool / workflow
    execution_mode: str           # preemptive(抢占) / standard(标准)
    preemptible: bool             # 是否可触发抢占
    expected_budget: float        # 预期预算(耗时×优先级修正)
    intent: TaskIntent = None
    features: TaskFeatures = None
    preemption_reason: str = None
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "priority": self.priority, "route_hint": self.route_hint,
            "execution_mode": self.execution_mode, "preemptible": self.preemptible,
            "expected_budget": self.expected_budget,
            "preemption_reason": self.preemption_reason, "reason": self.reason,
            "intent": self.intent.category if self.intent else None,
        }


class CognitiveScheduler:
    """意图感知的认知调度器。"""

    # 意图识别关键词(粗分类)
    _REASONING = ["规划", "计划", "分析", "设计", "推理", "为什么", "怎么办", "策略", "方案"]
    _MEMORY = ["记得", "记忆", "之前", "上次", "回忆", "查一下我", "我说过"]
    _WORKFLOW = ["然后", "接着", "之后", "依次", "批量", "流程", "再然后", "下一步"]

    def parse_intent(self, task: str) -> TaskIntent:
        t = (task or "").lower()
        if any(k in t for k in self._REASONING):
            return TaskIntent("reasoning", 0.8, 0.6, 0.95)
        if any(k in t for k in self._MEMORY):
            return TaskIntent("memory", 0.85, 0.9, 0.4)
        if any(k in t for k in self._WORKFLOW):
            return TaskIntent("workflow", 0.75, 0.5, 0.6)
        return TaskIntent("tool", 0.7, 0.2, 0.3)

    def extract_features(self, task: str, intent: TaskIntent) -> TaskFeatures:
        complexity, tool_cost, urgency, runtime = 0.4, 0.3, 0.6, 0.5
        if intent.category == "reasoning":
            complexity, tool_cost, urgency, runtime = 0.9, 0.4, 0.8, 0.9
        elif intent.category == "workflow":
            complexity, tool_cost, urgency, runtime = 0.7, 0.6, 0.65, 0.8
        elif intent.category == "memory":
            complexity, tool_cost, urgency, runtime = 0.5, 0.2, 0.55, 0.6
        # 紧急词加成
        if any(w in task for w in ["紧急", "立刻", "马上", "尽快", "急"]):
            urgency = min(1.0, urgency + 0.3)
        return TaskFeatures(complexity, intent.memory_affinity, tool_cost,
                            intent.reasoning_depth, urgency, runtime)

    def score(self, f: TaskFeatures) -> float:
        """基础分:加权特征。"""
        weighted = (f.complexity * 0.3 + f.memory_dependency * 0.2
                    + f.tool_cost * 0.15 + f.reasoning_depth * 0.25
                    + f.urgency * 0.1)
        return round(weighted, 3)

    def adjust(self, base: float, ctx: SystemContext, intent: TaskIntent,
               f: TaskFeatures) -> float:
        """负载调整:负载/队列越深,优先级被压低;紧急/记忆亲和度抬升。"""
        memory_affinity = max(0.25, intent.memory_affinity)
        load_penalty = 1 + ctx.system_load + (ctx.queue_depth * 0.05)
        urgency_boost = max(0.5, f.urgency)
        final = (base * urgency_boost * memory_affinity) / load_penalty
        return min(1.0, round(final, 3))

    def route_hint(self, priority: float, intent: TaskIntent) -> str:
        if intent.category == "workflow":
            return "workflow"
        if intent.category == "reasoning":
            return "agent"
        if intent.category == "memory" and priority < 0.3:
            return "tool"
        return "agent" if priority > 0.35 else "tool"

    def preemption_reason(self, priority: float, ctx: SystemContext) -> str:
        # 阈值匹配实际优先级范围(本调度器优先级多在 0.4 以内,
        # 原 0.55/0.7 几乎不可达,会让抢占形同虚设)
        if priority > 0.35 and ctx.active_agents >= 3:
            return "高优先级 + Agent 压力大"
        if priority > 0.4 and ctx.queue_depth >= 2:
            return "高优先级 + 队列积压"
        return None

    def schedule(self, task: str, ctx: SystemContext = None) -> ScheduleDecision:
        """主入口:给一个任务做完整调度决策。"""
        ctx = ctx or SystemContext()
        intent = self.parse_intent(task)
        features = self.extract_features(task, intent)
        base = self.score(features)
        priority = self.adjust(base, ctx, intent, features)
        route = self.route_hint(priority, intent)
        preempt = self.preemption_reason(priority, ctx)
        mode = "preemptive" if priority > 0.4 else "standard"
        return ScheduleDecision(
            priority=priority, route_hint=route, execution_mode=mode,
            preemptible=preempt is not None,
            expected_budget=round(features.expected_runtime * (1 + priority), 2),
            intent=intent, features=features, preemption_reason=preempt,
            reason=f"意图={intent.category} 优先级={priority} 模式={mode}")
