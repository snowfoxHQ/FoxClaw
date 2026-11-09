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

# ── S3 步骤 4：按检测维度拆分（纯搬移、零行为变更）────────────────────────
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

# 历史导入兼容：`from foxclaw.harness.harness import X` 的 X 在此 re-export。
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
        spine=None,                       # 【P0】全局 EventSpine(可空:standalone/governance 场景)
        promote_min_obs: int | None = None,      # 【P0-1】影子规则升迁所需观察次数(None→env/默认 50)
    ):
        # embedding 模型解析 —— 【P2·单 embedding 入口】优先级:
        #   1. 显式传参(测试/单 Agent 切模型用)
        #   2. EMBEDDING_MODEL(系统唯一本地 embedding 入口,.env 一处配 bge-m3 全局生效)
        #   3. HKC_EMBEDDING_MODEL(向后兼容,EMBEDDING_MODEL 未设时回退)
        #   4. FOXCLAW_EMBEDDING_MODEL(向后兼容)
        #   5. all-MiniLM-L6-v2(传统兜底)
        # 中文部署设 paraphrase-multilingual-MiniLM-L12-v2 时,语义漂移对中文硬拦率
        # 从 0 升到 1.0(见 evals/governance/compare_embeddings.py)。
        embedding_model = (embedding_model
                           or os.environ.get("EMBEDDING_MODEL")
                           or os.environ.get("HKC_EMBEDDING_MODEL")
                           or os.environ.get("FOXCLAW_EMBEDDING_MODEL")
                           or "all-MiniLM-L6-v2")
        self.agent_id = agent_id
        self.thought_chain = thought_chain
        self.policy_path = Path(policy_path).resolve()
        self.shadow_rounds = shadow_rounds
        # per-实例分槽 token(目标基线走 contextvar,见文件顶部)
        self._ctx_token = uuid.uuid4().hex
        # 累积学习态(_delta_history/_run_count/weights/_policy/阈值)的并发锁。
        # 为把请求 offload 到线程池提速做准备:多线程会并发调 check 打同一 harness,
        # 昂贵的语义嵌入放锁外(线程安全、可并行),只有廉价的累积态改写进锁。RLock:
        # _trigger_policy_learning 在锁内调用,同线程可重入。
        self._lock = threading.RLock()

        # 检测引擎
        self.sem_drift = SemanticDrift(embedding_model)
        self.tool_drift = ToolIntentDrift()
        self.scope_drift = ScopeCreepDrift()

        # 事件总线:本地订阅(向后兼容)+ 转发进全局脊柱(spine 非空时)
        self.bus = HarnessEventBus(spine=spine, source=agent_id)

        # 目标基线(每任务)—— 由 contextvar 承载;_baseline 是路由到 contextvar 的
        # property,register_goal/check 里对 self._baseline 的读写代码完全不变。
        self._baseline = None

        # 权重（Policy 学习后动态调整）
        self.weights = {"semantic": 0.5, "tool": 0.3, "scope": 0.2}

        # 是否因「语义漂移」而 BLOCK。领域 Agent=True(执行业务任务时跑题要拦);
        # 调度层(主管)=False(接收任意输入是其职责,语义距离大不算异常)。
        self.block_on_semantic = True

        # ── 降级方向修正(B 类修复)────────────────────────────────────
        # 语义嵌入不可用时(SemanticDrift.mode == "hash"),3-gram 哈希对中文几乎
        # 无区分度 —— 实测同域相似度 -0.17、跨域 +0.00,差异 <0.17 → 漂移分
        # 1-sim 裁剪后**恒为 1.0**。该维度一旦参与加权(semantic 权重 0.5),
        # Δ 恒 0.5 > block 阈值 0.25 → **所有请求一律 BLOCK**,包括评测集里
        # ground-truth 标注为 pass 的正常请求。
        # 治理层从「精准拦截」退化为「无差别拦截」,比不治理更危险(阻塞全部正常
        # 业务),且失败是静默的(仅 mode 属性变化,无告警)。
        #
        # 正确处置:安全组件的降级方向必须是 **fail-open(失效但可见)**,
        # 而不是 fail-closed-with-garbage(过度拦截且静默)。故降级时**停用语义
        # 维度**,而 tool/scope 两个维度是确定性规则(工具类别匹配、实体集合运算),
        # 不依赖模型质量,降级后仍可信,照常工作。
        self.semantic_degraded: bool = (self.sem_drift.mode != "transformer")
        # 降级只告警一次,避免每节点刷屏
        self._degrade_warned: bool = False

        # Shadow mode 状态
        self._shadow_mode = True
        self._run_count = 0
        self._delta_history: list[float] = []

        # 阈值（shadow mode 结束后用真实数据替换）
        self._warn_threshold = 0.15
        self._block_threshold = 0.25

        # Policy
        self._policy: dict = self._load_policy()
        # 【P0-1】影子学习段(旧 YAML 没有 → 补空段;不重命名既有键,版本兼容)
        self._policy.setdefault("shadow_rules", [])
        self._policy.setdefault("history", [])

        # 【P0-1】影子观察达标线:observations 凑满才做 promote/discard 判定。
        # 显式构造参数 > 环境变量 > 默认 50(文稿建议值)。个人级低频使用下一条影子
        # 规则可能长期 observing —— 这正是影子的目的:证据不够就不生效。
        if promote_min_obs is not None:
            self.promote_min_obs = promote_min_obs
        else:
            _env = os.environ.get("FOXCLAW_SHADOW_PROMOTE_MIN_OBS", "").strip()
            self.promote_min_obs = int(_env) if _env.isdigit() else 50

        # 把已学习规则的 action 编译成 check() 可直接应用的结构(规则级生效)
        self._penalized_tools: set[str] = set()
        self._scope_limit: Optional[int] = None
        self._reindex_learned_actions()

        # 注册默认事件处理
        self._setup_default_handlers()

    # ── 目标基线(每任务,contextvar 支撑,property 透明路由)──────
    @property
    def _baseline(self):
        return _harness_ctx_get(self._ctx_token)

    @_baseline.setter
    def _baseline(self, value):
        _harness_ctx_set(self._ctx_token, value)

    # ── 目标基线注册 ───────────────────────────────────────

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
        # 用 encode(纯函数)算每任务自持的基线向量,不写共享的 _baseline_vec →
        # 并发顶层任务的语义基线互不覆盖(check 用 self._baseline.goal_embedding 打分)。
        baseline.goal_embedding = self.sem_drift.encode(goal_text)
        self._baseline = baseline
        self.bus.emit("goal_registered", baseline.to_dict())
        return baseline

    # ── 核心校验方法 ───────────────────────────────────────

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
        baseline = self._baseline    # contextvar 读(每任务/线程独立)
        if baseline is None:
            # 没有注册目标基线，只观测不截断
            result = HarnessResult(
                status="PASS", delta=0.0, dominant_dim="none",
                scores={"semantic": 0.0, "tool": 0.0, "scope": 0.0},
            )
            self.thought_chain.update_harness(node.id, "PASS")
            return result

        if self.semantic_degraded and not ignore_semantic:
            return self._check_degraded(node)

        # 语义嵌入(昂贵、线程安全)—— **放锁外**,使请求 offload 到线程池后,并发请求的
        # 嵌入计算真正并行(torch/ST CPU 前向会释放 GIL)。这是吞吐提速的关键点。
        sem_score = 0.0 if ignore_semantic else \
            self.sem_drift.score(node.content, baseline.goal_embedding)

        # 以下累积态的读改写进锁(廉价):weights/_delta_history/_run_count/阈值/_policy。
        with self._lock:
            scores = {
                "semantic": sem_score,
                "tool": self.tool_drift.score(
                    node.tool_name or "", baseline.allowed_tool_categories
                ) if node.tool_name else 0.0,
                "scope": self.scope_drift.score(
                    set(node.entities), baseline.baseline_entities),
            }

            # 应用已学习规则的 action(规则级生效,区别于权重级自学习):
            #   · penalize_tool —— 之前触发过 BLOCK 的工具,即便类别本在允许集里,也判最高
            #     工具风险(1.0),让「学到某工具危险」下次真正拦得住,而非只微调 tool 权重。
            #   · restrict_scope —— 学到的实体扩张上限;新增实体超限直接判范围越界(scope=1.0)。
            if node.tool_name and self._is_penalized_tool(node.tool_name):
                scores["tool"] = 1.0
            if (self._scope_limit is not None and baseline.baseline_entities):
                new_entities = set(node.entities) - baseline.baseline_entities
                if len(new_entities) > self._scope_limit:
                    scores["scope"] = 1.0

            delta = sum(self.weights[k] * v for k, v in scores.items())
            dominant = max(scores, key=lambda k: scores[k])

            # 更新 shadow mode 历史。【深审修复·长期运行泄漏】原先**无条件**每次 check 都 append,
            # 但 _delta_history 仅在 _calibrate_thresholds(shadow 结束时跑一次)被读;shadow_mode 只在
            # init 置 True、永不重入,故校准后继续 append 是**纯无界泄漏**(每个推理节点一个 float)。
            # 改为**仅 shadow 期 append**:样本上限=shadow_rounds,校准看到的样本完全一致(行为不变)。
            if self._shadow_mode:
                self._delta_history.append(delta)
            self._run_count += 1

            # Shadow mode 结束：用真实数据校准阈值
            if self._shadow_mode and self._run_count >= self.shadow_rounds:
                self._calibrate_thresholds()

            # 判断状态
            if self._shadow_mode:
                status: HarnessStatus = "PASS"  # 观测期永不截断
                reason = None
            elif delta > self._block_threshold:
                # 调度层(如主管)豁免「语义漂移」拦截:它的职责就是接收任意输入,
                # 语义距离天然大,不应因此 BLOCK。但工具越权/范围越界仍然拦截。
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

            # 【P0-1】影子规则双轨观察(锁内):用**已算好的 scores** 模拟应用影子规则的
            # action,只记录 would_block/would_pass,绝不改变上面的 real result。
            # 不重新做语义嵌入 —— 开销是 O(观察中规则数) 的纯算术,远优于文稿预估的 2x。
            # 顺序有意**先于**下方的 BLOCK 学习:本次 check 是对**既有**候选的一次观察样本;
            # 本次 BLOCK 诞生的新候选从**下一次** check 才开始积累证据(诞生事件≠观察样本)。
            if self._policy.get("shadow_rules"):
                self._observe_shadow_rules(node, result, scores)

            # BLOCK：触发 Policy 学习(mutates weights/_policy,须在锁内)
            if status == "BLOCK":
                self._trigger_policy_learning(node, result)

        # 锁外:SQLite 写(WAL 线程安全)+ 事件广播(HarnessEventBus 独立)
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
            # 一次性告警:降级 = 治理能力缺失,属需人工知晓的事件,不能只靠属性静默。
            import logging as _lg
            _lg.getLogger("foxclaw.harness").warning(
                "[harness] 语义嵌入不可用(mode=%s),语义漂移维度已停用 —— "
                "漂移检测暂退化为 tool/scope 两维,降级期间跑题不会被拦截。"
                "恢复:pip install sentence-transformers",
                self.sem_drift.mode)

        with self._lock:
            # 降级期间不污染 shadow 样本:把语义分置 0 后再 append,否则恒 1.0 的
            # 语义分会污染阈值校准,装回 ST 后阈值仍不准。
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
                # 规则级学习(action)照常生效 —— 这部分不依赖 embedding
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
                    status: HarnessStatus = "PASS"  # 观测期永不截断
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
                # 降级标记:让上层/前端能识别「本次判定不含语义维度」(失效但可见)
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

        # 锁外:SQLite 写 + 事件广播(同 check,额外带 degraded 标记)
        self.thought_chain.update_harness(node.id, status, reason, delta, dominant)
        self.bus.emit(f"harness_{status.lower()}", {
            "agent_id": self.agent_id, "node_id": node.id, "node_type": node.type,
            "result": result.__dict__, "degraded": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return result

    # ── 默认事件处理 ───────────────────────────────────────

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

    # ── 状态查询 ───────────────────────────────────────────

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
            # ── 降级可见性(B 类修复)────────────────────────────────
            # 语义嵌入是否降级。semantic_degraded=True 意味着漂移检测只剩
            # tool/scope 两维,**跑题不会被拦截** —— 这是需要人工知晓的治理
            # 能力缺失,不能只体现在日志里。
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
            # 【P0-1】影子学习账本:observing=观察中(不生效);discarded/rolled_back 保留供审计
            "shadow_observing": sum(1 for r in shadow
                                    if (r.get("shadow") or {}).get("status") == "observing"),
            "shadow_total": len(shadow),
            "history_events": len(self._policy.get("history", [])),
            # 返回**副本**:此前直接返回 self.weights 引用 —— 调用方拿到的不是快照,
            # 既可能被后续权重更新"隔空改写"(读到的值取决于时机),也能反向改坏内部状态。
            "weights": dict(self.weights),
        }
