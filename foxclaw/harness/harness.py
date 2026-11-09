"""
Harness — 跨层约束与校准机制
不是某一层的模块，而是每一层都能感知的治理张力

三个检测维度：
  1. 语义距离 (semantic)  — 节点内容 vs 目标基线
  2. 工具意图 (tool)      — 工具类别 vs 允许集合
  3. 范围蔓延 (scope)     — 当前实体 vs 初始实体集

Shadow mode：前 N 次只观测不截断，用真实数据校准阈值
"""


import os
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..core.thought_chain import ThoughtChain, ThoughtNode, HarnessStatus

from ._base import (
    HarnessResult,
    GoalBaseline,
    TOOL_CATEGORIES,
    _harness_ctx_get,
    _harness_ctx_set,
)
from .drift import SemanticDrift, ToolIntentDrift, ScopeCreepDrift
from .event_bus import HarnessEventBus
from ._policy import PolicyMixin

__all__ = [
    "Harness", "HarnessResult", "GoalBaseline",
    "SemanticDrift", "ToolIntentDrift", "ScopeCreepDrift",
    "HarnessEventBus", "TOOL_CATEGORIES",
]


class Harness(PolicyMixin):
    """
    Harness 主引擎
    每个 Agent 实例化一个，共享 ThoughtChain 存储
    """
    def __init__(
        self,
        agent_id: str,
        thought_chain: ThoughtChain,
        policy_path: str = "./foxclaw_data/policy.yaml",
        shadow_rounds: int = 20,
        embedding_model: str | None = None,
        spine=None,
        promote_min_obs: int | None = None,
    ):
        embedding_model = (embedding_model
                           or os.environ.get("EMBEDDING_MODEL")
                           or os.environ.get("HKC_EMBEDDING_MODEL")
                           or os.environ.get("FOXCLAW_EMBEDDING_MODEL")
                           or "all-MiniLM-L6-v2")
        self.agent_id = agent_id
        self.thought_chain = thought_chain
        self.policy_path = Path(policy_path).resolve()
        self.shadow_rounds = shadow_rounds
        self._ctx_token = uuid.uuid4().hex
        self._lock = threading.RLock()

        self.sem_drift = SemanticDrift(embedding_model)
        self.tool_drift = ToolIntentDrift()
        self.scope_drift = ScopeCreepDrift()

        self.bus = HarnessEventBus(spine=spine, source=agent_id)

        self._baseline = None

        self.weights = {"semantic": 0.5, "tool": 0.3, "scope": 0.2}

        self.block_on_semantic = True

        self.semantic_degraded: bool = (self.sem_drift.mode != "transformer")
        self._degrade_warned: bool = False

        self._shadow_mode = True
        self._run_count = 0
        self._delta_history: list[float] = []

        self._warn_threshold = 0.15
        self._block_threshold = 0.25

        self._policy: dict = self._load_policy()
        self._policy.setdefault("shadow_rules", [])
        self._policy.setdefault("history", [])

        if promote_min_obs is not None:
            self.promote_min_obs = promote_min_obs
        else:
            _env = os.environ.get("FOXCLAW_SHADOW_PROMOTE_MIN_OBS", "").strip()
            self.promote_min_obs = int(_env) if _env.isdigit() else 50

        self._penalized_tools: set[str] = set()
        self._scope_limit: Optional[int] = None
        self._reindex_learned_actions()

        self._setup_default_handlers()

    @property
    def _baseline(self):
        return _harness_ctx_get(self._ctx_token)

    @_baseline.setter
    def _baseline(self, value):
        _harness_ctx_set(self._ctx_token, value)


    def register_goal(
        self,
        goal_text: str,
        allowed_tool_categories: set[str],
        baseline_entities: set[str] | None = None,
    ) -> GoalBaseline:
        """接口层调用 — 注册任务目标基线"""
        baseline = GoalBaseline(
            goal_text=goal_text,
            allowed_tool_categories=allowed_tool_categories,
            baseline_entities=baseline_entities or set(),
        )
        baseline.goal_embedding = self.sem_drift.encode(goal_text)
        self._baseline = baseline
        self.bus.emit("goal_registered", baseline.to_dict())
        return baseline


    def check(self, node: ThoughtNode, ignore_semantic: bool = False) -> HarnessResult:
        """
        对一个 ThoughtNode 做 Harness 校验
        返回 HarnessResult，并更新节点的 harness_status

        ignore_semantic=True:把语义维度置 0(只按 tool/scope 判定)。用于"工具授权"这类
        场景 —— 合成的工具调用串对目标句的语义距离是噪声,不该作为拦截依据。默认 False,
        不影响任何既有调用方的行为。

        【B 类修复】语义嵌入降级时(semantic_degraded=True):语义维度**停用**而非
        继续参与判定。哈希嵌入无区分度 → 漂移分恒 1.0 → 若继续加权会把所有正常
        请求一律 BLOCK(fail-closed-with-garbage)。降级方向应为 fail-open:
        停用不可信维度,保留可信的 tool/scope 判定,并在 reason 显式标注降级
        (失效但可见)。走 _check_degraded 分支,正常路径逻辑逐字不变。
        """
        baseline = self._baseline
        if baseline is None:
            result = HarnessResult(
                status="PASS", delta=0.0, dominant_dim="none",
                scores={"semantic": 0.0, "tool": 0.0, "scope": 0.0},
            )
            self.thought_chain.update_harness(node.id, "PASS")
            return result

        if self.semantic_degraded and not ignore_semantic:
            return self._check_degraded(node)

        sem_score = 0.0 if ignore_semantic else \
            self.sem_drift.score(node.content, baseline.goal_embedding)

        with self._lock:
            scores = {
                "semantic": sem_score,
                "tool": self.tool_drift.score(
                    node.tool_name or "", baseline.allowed_tool_categories
                ) if node.tool_name else 0.0,
                "scope": self.scope_drift.score(
                    set(node.entities), baseline.baseline_entities),
            }

            if node.tool_name and self._is_penalized_tool(node.tool_name):
                scores["tool"] = 1.0
            if (self._scope_limit is not None and baseline.baseline_entities):
                new_entities = set(node.entities) - baseline.baseline_entities
                if len(new_entities) > self._scope_limit:
                    scores["scope"] = 1.0

            delta = sum(self.weights[k] * v for k, v in scores.items())
            dominant = max(scores, key=lambda k: scores[k])

            if self._shadow_mode:
                self._delta_history.append(delta)
            self._run_count += 1

            if self._shadow_mode and self._run_count >= self.shadow_rounds:
                self._calibrate_thresholds()

            if self._shadow_mode:
                status: HarnessStatus = "PASS"
                reason = None
            elif delta > self._block_threshold:
                if not self.block_on_semantic and dominant == "semantic":
                    status = "WARN"
                    reason = f"semantic drift Δ={delta:.3f}(调度层仅告警不拦截)"
                else:
                    status = "BLOCK"
                    reason = f"{dominant} drift={scores[dominant]:.3f} Δ={delta:.3f}"
            elif delta > self._warn_threshold:
                status = "WARN"
                reason = f"{dominant} approaching threshold Δ={delta:.3f}"
            else:
                status = "PASS"
                reason = None

            result = HarnessResult(
                status=status, delta=delta, dominant_dim=dominant,
                scores=scores, reason=reason,
            )

            if self._policy.get("shadow_rules"):
                self._observe_shadow_rules(node, result, scores)

            if status == "BLOCK":
                self._trigger_policy_learning(node, result)

        self.thought_chain.update_harness(node.id, status, reason, delta, dominant)
        self.bus.emit(f"harness_{status.lower()}", {
            "agent_id": self.agent_id, "node_id": node.id, "node_type": node.type,
            "result": result.__dict__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return result

    def _check_degraded(self, node: ThoughtNode) -> HarnessResult:
        """语义嵌入降级时的判定:停用语义维度,保留 tool/scope 确定性判定。

        与 check() 的差异**仅在于**:跳过语义嵌入计算(省钱省时)且 semantic 权重
        按 0 计入。锁内累加/阈值校准/影子学习复用同一套逻辑,保证 shadow 学习与
        阈值校准行为与正常路径一致(不因降级而产生第二套语义)。

        为何不能只调阈值:无区分度时阈值怎么调都是错的 —— 高阈值漏放跑题,
        低阈值拦截正常请求。必须**停用该维度**。
        为何保留 tool/scope:两者是确定性规则(工具类别匹配 / 实体集合运算),
        不依赖模型质量,降级后仍可信。
        """
        baseline = self._baseline
        if not self._degrade_warned:
            self._degrade_warned = True
            import logging as _lg
            _lg.getLogger("foxclaw.harness").warning(
                "[harness] 语义嵌入不可用(mode=%s),语义漂移维度已停用 —— "
                "漂移检测暂退化为 tool/scope 两维,降级期间跑题不会被拦截。"
                "恢复:pip install sentence-transformers",
                self.sem_drift.mode)

        with self._lock:
            saved_w = self.weights.get("semantic", 0.0)
            self.weights["semantic"] = 0.0
            try:
                scores = {
                    "semantic": 0.0,
                    "tool": self.tool_drift.score(
                        node.tool_name or "", baseline.allowed_tool_categories
                    ) if node.tool_name else 0.0,
                    "scope": self.scope_drift.score(
                        set(node.entities), baseline.baseline_entities),
                }
                if node.tool_name and self._is_penalized_tool(node.tool_name):
                    scores["tool"] = 1.0
                if (self._scope_limit is not None and baseline.baseline_entities):
                    new_entities = set(node.entities) - baseline.baseline_entities
                    if len(new_entities) > self._scope_limit:
                        scores["scope"] = 1.0

                delta = sum(self.weights[k] * v for k, v in scores.items())
                dominant = max(scores, key=lambda k: scores[k])
                if self._shadow_mode:
                    self._delta_history.append(delta)
                self._run_count += 1
                if self._shadow_mode and self._run_count >= self.shadow_rounds:
                    self._calibrate_thresholds()

                if self._shadow_mode:
                    status: HarnessStatus = "PASS"
                    reason = None
                elif delta > self._block_threshold:
                    status = "BLOCK"
                    reason = f"{dominant} drift={scores[dominant]:.3f} Δ={delta:.3f}"
                elif delta > self._warn_threshold:
                    status = "WARN"
                    reason = f"{dominant} approaching threshold Δ={delta:.3f}"
                else:
                    status = "PASS"
                    reason = None
                reason = ((reason + " [semantic-degraded:治理降级]") if reason
                          else "[semantic-degraded:语义维度停用]")
            finally:
                self.weights["semantic"] = saved_w

            result = HarnessResult(
                status=status, delta=delta, dominant_dim=dominant,
                scores=scores, reason=reason,
            )
            if self._policy.get("shadow_rules"):
                self._observe_shadow_rules(node, result, scores)
            if status == "BLOCK":
                self._trigger_policy_learning(node, result)

        self.thought_chain.update_harness(node.id, status, reason, delta, dominant)
        self.bus.emit(f"harness_{status.lower()}", {
            "agent_id": self.agent_id, "node_id": node.id, "node_type": node.type,
            "result": result.__dict__, "degraded": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return result


    def _setup_default_handlers(self):
        def on_block(data):
            from rich.console import Console
            Console().print(
                f"[bold red][HARNESS BLOCK][/] agent={data['agent_id']} "
                f"node={data['node_id']} reason={data['result']['reason']}"
            )

        def on_policy_updated(data):
            from rich.console import Console
            Console().print(
                f"[bold yellow][POLICY UPDATE][/] agent={data['agent_id']} "
                f"rule={data['rule_id']} weights={data['new_weights']}"
            )

        self.bus.on("harness_block", on_block)
        self.bus.on("policy_updated", on_policy_updated)


    @property
    def is_shadow_mode(self) -> bool:
        return self._shadow_mode

    @property
    def thresholds(self) -> dict:
        return {
            "warn": self._warn_threshold,
            "block": self._block_threshold,
            "shadow_mode": self._shadow_mode,
            "run_count": self._run_count,
            "shadow_rounds": self.shadow_rounds,
            "semantic_degraded": self.semantic_degraded,
            "embedding_mode": self.sem_drift.mode,
        }

    def get_policy_summary(self) -> dict:
        rules = self._policy.get("rules", [])
        shadow = self._policy.get("shadow_rules", [])
        return {
            "version": self._policy.get("version", 1),
            "total_rules": len(rules),
            "manual_rules": sum(1 for r in rules if r.get("source") == "manual"),
            "learned_rules": sum(1 for r in rules if r.get("source") == "harness_learned"),
            "shadow_observing": sum(1 for r in shadow
                                    if (r.get("shadow") or {}).get("status") == "observing"),
            "shadow_total": len(shadow),
            "history_events": len(self._policy.get("history", [])),
            "weights": dict(self.weights),
        }
