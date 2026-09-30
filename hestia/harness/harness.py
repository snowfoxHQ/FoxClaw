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
import contextvars
import numpy as np
import yaml
import json
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Optional
from sklearn.metrics.pairwise import cosine_similarity

from ..core.thought_chain import ThoughtChain, ThoughtNode, HarnessStatus


# 目标基线是**每任务**语义(register_goal 每次任务设一次),却存在 Harness 实例上——
# 并发顶层任务打同一 Agent 的 harness 时会互相覆盖,导致 check 对着错误的目标算漂移。
# 与 base.py 同构:放进单个 module 级 contextvar 承载的 dict、按实例 token 分槽。
# 累积学习态(weights/_policy/_delta_history/_run_count/_shadow_mode)仍留在实例上——
# 那本就该按 Agent 累积,且同步 check 内无 await,协作调度下读改写原子安全。
_harness_ctx: contextvars.ContextVar = contextvars.ContextVar(
    "hestia_harness_ctx", default=None)


def _harness_ctx_get(token: str):
    return (_harness_ctx.get() or {}).get(token)


def _harness_ctx_set(token: str, baseline):
    d = dict(_harness_ctx.get() or {})
    d[token] = baseline
    _harness_ctx.set(d)

# 文本向量由 hestia.capability.embedding_service 统一提供(进程内共享,同名只加载一次)。
# 不可用时该服务返回 None,SemanticDrift 自动降级为 n-gram 哈希嵌入。


# ── 工具类别白名单 ─────────────────────────────────────────
TOOL_CATEGORIES: dict[str, set[str]] = {
    "read":    {"read_file", "search", "fetch", "list", "get", "query"},
    "write":   {"write_file", "create", "update", "save", "append"},
    "delete":  {"delete", "remove", "drop", "clear", "reset"},
    "exec":    {"run_code", "shell", "subprocess", "execute"},
    "network": {"http_get", "http_post", "scrape", "download"},
    "calendar":{"add_event", "get_schedule", "update_event"},
    "finance": {"get_balance", "add_transaction", "generate_report"},
}


@dataclass
class HarnessResult:
    status: HarnessStatus
    delta: float
    dominant_dim: str          # 主导漂移维度
    scores: dict               # 各维度分数
    reason: Optional[str] = None

    @property
    def is_blocked(self) -> bool:
        return self.status == "BLOCK"

    @property
    def is_warned(self) -> bool:
        return self.status == "WARN"


@dataclass
class GoalBaseline:
    """目标基线 — 在接口层注册，后续所有层的校验参照此基线"""
    goal_text: str
    allowed_tool_categories: set[str]
    baseline_entities: set[str]
    goal_embedding: Optional[np.ndarray] = None

    def to_dict(self) -> dict:
        return {
            "goal_text": self.goal_text,
            "allowed_tool_categories": list(self.allowed_tool_categories),
            "baseline_entities": list(self.baseline_entities),
        }


class SemanticDrift:
    """维度一：语义距离。优先 sentence-transformers,不可用则降级 n-gram 哈希嵌入。"""

    DIM = 256

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        # P0:走进程内共享 EmbeddingService,同名模型全系统只加载一次
        # (原来每个 Harness 各 load 一份;5 Agent = 5 份 → 现 1 份共享,只读并发 encode 安全)。
        from ..capability.embedding_service import get_text_embedder
        self._model = get_text_embedder(model_name)
        self._mode = "transformer" if self._model is not None else "hash"

    @property
    def mode(self) -> str:
        return self._mode

    def _encode(self, text: str) -> np.ndarray:
        if self._mode == "transformer":
            return self._model.encode(text)
        return self._hash_embed(text)

    def _hash_embed(self, text: str) -> np.ndarray:
        """字符 3-gram 哈希到固定维向量。余弦相似度数学真实,只是不带语义模型。"""
        vec = np.zeros(self.DIM, dtype=np.float32)
        text = (text or "").lower().strip()
        if not text:
            return vec
        grams = [text[i:i+3] for i in range(max(len(text) - 2, 1))]
        for g in grams:
            h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
            idx = h % self.DIM
            sign = 1.0 if (h // self.DIM) % 2 == 0 else -1.0
            vec[idx] += sign
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    def encode(self, text: str) -> np.ndarray:
        """把文本编码成向量(纯函数,无副作用)。每任务基线自持此向量,并发安全。"""
        return self._encode(text)

    def score(self, content: str, baseline_vec: np.ndarray) -> float:
        """内容相对**每任务基线向量**的语义漂移。baseline_vec 由调用方(check)显式传入,
        不再用共享的实例级基线 → 并发顶层任务的语义基线互不覆盖。"""
        if baseline_vec is None:
            return 0.0
        node_vec = self._encode(content)
        sim = cosine_similarity([baseline_vec], [node_vec])[0][0]
        # 余弦相似度范围 [-1, 1],哈希嵌入带符号累加可能产生负相似度,
        # 1 - sim 会超过 1.0 → 必须双向裁剪到 [0, 1],否则漂移分超出阈值范围使判定失真。
        return float(min(1.0, max(0.0, 1.0 - sim)))


class ToolIntentDrift:
    """维度二：工具意图"""

    def score(self, tool_name: str, allowed_categories: set[str]) -> float:
        if not tool_name:
            return 0.0
        for cat, patterns in TOOL_CATEGORIES.items():
            if any(tool_name.startswith(p) or p in tool_name for p in patterns):
                return 0.0 if cat in allowed_categories else 1.0
        return 0.3  # 未知工具：中等风险


class ScopeCreepDrift:
    """维度三：范围蔓延"""

    def score(self, node_entities: set[str], baseline_entities: set[str]) -> float:
        # 空基线返回 0 是有意设计:范围蔓延是"相对基线的偏离",没有基线参照就无从
        # 谈蔓延。若此处对空基线返回高分,会让所有尚未建立实体基线的早期任务被误判为
        # 范围越界(误报)。范围检测的有效性依赖基线实体提取——若该维度长期为 0,
        # 应排查实体提取(register_goal 的 entities)是否为空,而非改动本函数。
        if not baseline_entities:
            return 0.0
        new_entities = node_entities - baseline_entities
        return min(len(new_entities) / max(len(baseline_entities), 1), 1.0)


class HarnessEventBus:
    """阶段一：事件总线（观测优先）"""

    # 高频且纯内部的信号不上脊柱,避免 WS 噪音(见审计"哪些事件不应该广播")
    _SPINE_SKIP = {"goal_registered"}

    def __init__(self, spine=None, source: str = ""):
        self._handlers: dict[str, list[Callable]] = {}
        self._spine = spine        # 【P0·统一脊柱】非空则把 emit 同时转发进全局 EventSpine
        self._source = source      # 事件来源(agent_id),便于投影侧区分

    def on(self, event: str, handler: Callable):
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, data: dict):
        # 1) 本地订阅者(向后兼容:agent.harness.bus.on / harness 自订阅 / governance)
        for h in self._handlers.get(event, []):
            try:
                h(data)
            except Exception as e:
                print(f"[Harness EventBus] handler error: {e}")
        # 2) 汇入全局脊柱:harness 事件与系统事件走同一条脊柱、被同一 WS 投影观测
        if self._spine is not None and event not in self._SPINE_SKIP:
            try:
                self._spine.publish(event, data, source=self._source or "harness")
            except Exception:
                pass


class Harness:
    """
    Harness 主引擎
    每个 Agent 实例化一个，共享 ThoughtChain 存储
    """

    def __init__(
        self,
        agent_id: str,
        thought_chain: ThoughtChain,
        policy_path: str = "./hestia_data/policy.yaml",
        shadow_rounds: int = 20,
        embedding_model: str = None,
        spine=None,                       # 【P0】全局 EventSpine(可空:standalone/governance 场景)
    ):
        # embedding 可经 HESTIA_EMBEDDING_MODEL 环境变量切换(体现"模型无关"):
        # 中文部署设成 paraphrase-multilingual-MiniLM-L12-v2,语义漂移对中文的硬拦率
        # 从 0 升到 1.0(见 evals/governance/compare_embeddings.py)。显式传参优先于环境变量。
        embedding_model = embedding_model or os.environ.get(
            "HESTIA_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
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

        # Shadow mode 状态
        self._shadow_mode = True
        self._run_count = 0
        self._delta_history: list[float] = []

        # 阈值（shadow mode 结束后用真实数据替换）
        self._warn_threshold = 0.15
        self._block_threshold = 0.25

        # Policy
        self._policy: dict = self._load_policy()

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

    # ── 学习规则的 action 编译 / 应用 ─────────────────────────

    def _reindex_learned_actions(self):
        """把 self._policy['rules'] 里已学习的 action 编译成 check() 用得上的结构。

        使自学习从「权重级」升级到「规则级」:BLOCK 时 _generate_policy_action 生成的
        penalize_tool / restrict_scope 不再只是躺在 YAML 里的意图,而是被这里编译、
        并在 check() 里真正改变判定。加载 policy 时 + 每次新学到规则后都会重建。
        """
        penalized: set[str] = set()
        scope_limit: Optional[int] = None
        for rule in self._policy.get("rules", []):
            action = rule.get("action") or {}
            atype = action.get("type")
            if atype == "penalize_tool":
                pat = action.get("tool_pattern")
                if pat:
                    penalized.add(pat)
            elif atype == "restrict_scope":
                lim = action.get("entity_expansion_limit")
                if isinstance(lim, int):
                    # 多条 restrict 取最严(最小)上限
                    scope_limit = lim if scope_limit is None else min(scope_limit, lim)
        self._penalized_tools = penalized
        self._scope_limit = scope_limit

    def _is_penalized_tool(self, tool_name: str) -> bool:
        """工具名是否命中已学习的 penalize_tool 规则(与 ToolIntentDrift 一致的子串匹配)。"""
        if not tool_name or not self._penalized_tools:
            return False
        return any(tool_name == p or p in tool_name for p in self._penalized_tools)

    # ── 目标基线注册 ───────────────────────────────────────

    def register_goal(
        self,
        goal_text: str,
        allowed_tool_categories: set[str],
        baseline_entities: set[str] = None,
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
            dominant = max(scores, key=scores.get)

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

            # BLOCK：触发 Policy 学习(mutates weights/_policy/_reindex,须在锁内)
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

    # ── Policy 学习 ────────────────────────────────────────

    def _trigger_policy_learning(self, node: ThoughtNode, result: HarnessResult):
        """BLOCK 事件 → 生成 Policy 规则候选 → 写回 YAML"""
        new_rule = {
            "id": f"rule_{node.id}",
            "trigger": {
                "harness_status": "BLOCK",
                "dominant_dim": result.dominant_dim,
                "delta_threshold": round(result.delta, 3),
            },
            "action": self._generate_policy_action(node, result),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": "harness_learned",
            "agent_id": self.agent_id,
        }

        # 合并到 Policy
        self._policy.setdefault("rules", []).append(new_rule)
        self._save_policy()
        # 重建学习规则索引 → 新学到的 action 立即在后续 check() 生效(规则级)
        self._reindex_learned_actions()

        # 更新权重：触发截断的维度权重 +δ
        dim = result.dominant_dim
        if dim in self.weights:
            self.weights[dim] = min(self.weights[dim] + 0.05, 0.8)
            # 重新归一化
            total = sum(self.weights.values())
            self.weights = {k: v / total for k, v in self.weights.items()}

        self.bus.emit("policy_updated", {
            "agent_id": self.agent_id,
            "rule_id": new_rule["id"],
            "dominant_dim": result.dominant_dim,
            "new_weights": self.weights,
        })

    def _generate_policy_action(self, node: ThoughtNode, result: HarnessResult) -> dict:
        """生成结构化策略动作,随 learned rule 持久化到 policy 文件。

        自学习现在**两级都生效**:
          · 权重级 —— _trigger_policy_learning 里对触发维度 +0.05 并归一化,影响后续 delta;
          · 规则级 —— 本函数生成的 action 由 _reindex_learned_actions 编译,并在 check() 里应用:
              - penalize_tool     → 命中工具的 tool 分被抬到 1.0(即便类别本在允许集里);
              - restrict_scope    → 新增实体数超过 entity_expansion_limit 时 scope 分抬到 1.0。
            (increase_semantic_threshold 仍主要靠权重级体现,未单列规则应用。)
        因此这些 action 不再只是"意图记录"——penalize_tool / restrict_scope 会真正改变判定。
        """
        dim = result.dominant_dim
        if dim == "tool" and node.tool_name:
            return {
                "type": "penalize_tool",
                "tool_pattern": node.tool_name,
                "weight_delta": -0.3,
            }
        elif dim == "scope":
            return {
                "type": "restrict_scope",
                "entity_expansion_limit": 2,
            }
        else:
            return {
                "type": "increase_semantic_threshold",
                "delta": -0.05,
            }

    # ── 阈值校准 ───────────────────────────────────────────

    def _calibrate_thresholds(self):
        """Shadow mode 结束 — 用真实 Δ 分布校准阈值。
        关键:阈值要设在"正常任务分布"之上,这样正常任务通过,
        只有真正离群的(如完全跑题)才被拦截。
        否则用中位数附近的分位数会拦掉一半正常流量。
        """
        arr = np.array(sorted(self._delta_history), dtype=float)
        if arr.size == 0:
            # 空历史(理论上不会发生,因调用前每次 check 都 append)→ 用默认阈值,避免 nan
            self._shadow_mode = False
            return
        mean = float(arr.mean())
        std = float(arr.std())
        p90 = float(np.percentile(arr, 90))
        # BLOCK 阈值取"高分位 / 均值+1.5σ"的较大者,再留 10% 余量,
        # 保证正常任务(集中在均值附近)不被误拦
        block = max(p90, mean + 1.5 * std) * 1.10
        # WARN 阈值取 block 的 0.8 倍,给出渐进预警区
        warn = block * 0.8
        # 下限保护:阈值不低于默认值,避免样本噪声把阈值压得过紧
        self._block_threshold = max(block, 0.25)
        self._warn_threshold = max(warn, 0.18)
        self._shadow_mode = False
        self.bus.emit("shadow_calibrated", {
            "agent_id": self.agent_id,
            "warn_threshold": self._warn_threshold,
            "block_threshold": self._block_threshold,
            "sample_size": len(arr),
            "mean": round(mean, 3),
            "std": round(std, 3),
        })

    # ── Policy 持久化 ──────────────────────────────────────

    def _load_policy(self) -> dict:
        if self.policy_path.exists():
            with open(self.policy_path) as f:
                return yaml.safe_load(f) or {}
        # 默认 Policy 骨架（格式从第一天锁定）
        return {
            "version": 1,
            "agent_id": self.agent_id,
            "rules": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def _save_policy(self):
        os.makedirs(self.policy_path.parent, exist_ok=True)
        self._policy["version"] = self._policy.get("version", 1) + 1
        self._policy["updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(self.policy_path, "w") as f:
            yaml.dump(self._policy, f, allow_unicode=True, sort_keys=False)

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
        }

    def get_policy_summary(self) -> dict:
        rules = self._policy.get("rules", [])
        return {
            "version": self._policy.get("version", 1),
            "total_rules": len(rules),
            "manual_rules": sum(1 for r in rules if r.get("source") == "manual"),
            "learned_rules": sum(1 for r in rules if r.get("source") == "harness_learned"),
            "weights": self.weights,
        }
