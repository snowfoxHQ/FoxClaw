"""Harness Policy 学习 + 影子观察 + 阈值校准 + 持久化（mixin）。

拆分自 harness.py：`Harness` 主类继承本 mixin，通过 `self.weights/_policy/_lock`
等实例状态协同工作。纯搬移、零行为变更。
"""
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

import numpy as np
import yaml

from ..core.thought_chain import ThoughtNode
from ._base import HarnessResult

if TYPE_CHECKING:
    from .event_bus import HarnessEventBus


class PolicyMixin:
    agent_id: str
    bus: "HarnessEventBus"
    policy_path: Path
    weights: dict[str, float]
    promote_min_obs: int
    _policy: dict
    _baseline: Any
    _delta_history: list[float]


    def _reindex_learned_actions(self):
        """把 self._policy['rules'] 里已学习的 action 编译成 check() 用得上的结构。

        使自学习从「权重级」升级到「规则级」:BLOCK 时 _generate_policy_action 生成的
        penalize_tool / restrict_scope 不再只是躺在 YAML 里的意图,而是被这里编译、
        并在 check() 里真正改变判定。加载 policy 时 + 每次新学到规则后都会重建。
        """
        penalized: set[str] = set()
        scope_limit: int | None = None
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
                    scope_limit = lim if scope_limit is None else min(scope_limit, lim)
        self._penalized_tools = penalized
        self._scope_limit = scope_limit

    def _is_penalized_tool(self, tool_name: str) -> bool:
        """工具名是否命中已学习的 penalize_tool 规则(与 ToolIntentDrift 一致的子串匹配)。"""
        if not tool_name or not self._penalized_tools:
            return False
        return any(tool_name == p or p in tool_name for p in self._penalized_tools)


    _LEARNABLE_RULE_DIMS = frozenset({"tool", "scope"})

    def _trigger_policy_learning(self, node: ThoughtNode, result: HarnessResult):
        """BLOCK 事件 → 规则候选进 **shadow_rules(不生效)** → 影子观察 → promote/discard。

        【P0-1】原为「BLOCK → 规则直接写 rules + reindex → 下次 check 立即生效」——
        在线无验证学习是真实风险(一次偶发 BLOCK 就永久改变判定)。改后:
          · 规则级:候选进 shadow_rules,观察 N 次达标才 promote(生效)/discard;
          · 权重级:保持现状(触发维度 +0.05 归一化)——文稿只影子化规则级,权重微调风险低;
          · 边界:semantic 仅权重级不生成规则;未知维度连权重都不学,只记录。
        """
        dim = result.dominant_dim

        if dim not in self.weights:
            self._record_history("skip", "",
                                 note=f"未知维度 '{dim}' 禁止自学习(fail-closed)")
            self._save_policy()
            return

        if dim in self._LEARNABLE_RULE_DIMS:
            new_rule: dict[str, Any] = {
                "id": f"rule_{node.id}",
                "trigger": {
                    "harness_status": "BLOCK",
                    "dominant_dim": dim,
                    "delta_threshold": round(result.delta, 3),
                },
                "action": self._generate_policy_action(node, result),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source": "harness_learned",
                "agent_id": self.agent_id,
                "shadow": {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "observations": 0, "would_block": 0, "would_pass": 0,
                    "status": "observing",
                },
            }
            self._policy.setdefault("shadow_rules", []).append(new_rule)
            self._record_history("propose", new_rule["id"],
                                 note=f"{dim} 维度 BLOCK → 影子观察")
            self.bus.emit("shadow_rule_proposed", {
                "agent_id": self.agent_id, "rule_id": new_rule["id"],
                "dominant_dim": dim,
                "action_type": new_rule["action"].get("type", ""),
            })

        bumped = dict(self.weights)
        bumped[dim] = min(bumped[dim] + 0.05, 0.8)
        total = sum(bumped.values())
        self.weights = {k: v / total for k, v in bumped.items()}

        self._save_policy()
        self.bus.emit("policy_updated", {
            "agent_id": self.agent_id,
            "rule_id": "",
            "dominant_dim": dim,
            "new_weights": dict(self.weights),
        })


    def _observe_shadow_rules(self, node: ThoughtNode, result: HarnessResult,
                              real_scores: dict):
        """对观察中的影子规则做双轨计数,达标后 promote/discard。锁内调用。

        适用性:当前场景的 dominant_dim 与规则 trigger.dominant_dim 相同。
        (trigger.delta_threshold 仅记录生成时的漂移强度,不作匹配条件——否则
        observations 几乎无法积累,影子机制会失去意义。)

        落盘节流:promote/discard 立即 save;普通计数每 10 次 save 一次(进程崩溃
        最多丢 9 次观察计数,可接受)——否则「有影子规则时每次 check 都写 YAML」,
        一次任务几十次 check 就是几十次磁盘写。
        """
        need_save = False
        for rule in list(self._policy.get("shadow_rules", [])):
            sh = rule.get("shadow") or {}
            if sh.get("status") != "observing":
                continue
            trig = rule.get("trigger") or {}
            if trig.get("dominant_dim") != result.dominant_dim:
                continue

            sh["observations"] = sh.get("observations", 0) + 1

            shadow_status = self._status_with_action(rule, node, real_scores)
            real_blocked = result.is_blocked
            shadow_blocked = shadow_status == "BLOCK"
            if shadow_blocked and not real_blocked:
                sh["would_block"] = sh.get("would_block", 0) + 1
            elif not shadow_blocked and real_blocked:
                sh["would_pass"] = sh.get("would_pass", 0) + 1

            verdict = self._should_promote(sh)
            if verdict is True:
                self._promote_rule(rule)
                need_save = True
            elif verdict is False:
                self._discard_rule(rule, "影子证据不达标")
                need_save = True
            elif sh["observations"] % 10 == 0:
                need_save = True
        if need_save:
            self._save_policy()

    def _status_with_action(self, rule: dict, node: ThoughtNode,
                            real_scores: dict) -> str:
        """模拟应用单条影子规则的 action,给出「若它生效」的判定(复用同一权重/阈值)。"""
        scores = dict(real_scores)
        action = rule.get("action") or {}
        atype = action.get("type")
        if atype == "penalize_tool":
            pat = action.get("tool_pattern", "")
            tool_name = getattr(node, "tool_name", "") or ""
            if pat and tool_name and (tool_name == pat or pat in tool_name):
                scores["tool"] = 1.0
        elif atype == "restrict_scope":
            lim = action.get("entity_expansion_limit")
            baseline = self._baseline
            if isinstance(lim, int) and baseline is not None and baseline.baseline_entities:
                new_entities = set(node.entities) - baseline.baseline_entities
                if len(new_entities) > lim:
                    scores["scope"] = 1.0
        delta = sum(self.weights[k] * v for k, v in scores.items())
        if delta > self._block_threshold:
            return "BLOCK"
        if delta > self._warn_threshold:
            return "WARN"
        return "PASS"

    def _should_promote(self, sh: dict):
        """影子证据判定(文稿第四步):True=promote / False=discard / None=继续观察。"""
        obs = sh.get("observations", 0)
        if obs < self.promote_min_obs:
            return None
        wb, wp = sh.get("would_block", 0), sh.get("would_pass", 0)
        if wb / max(obs, 1) > 0.3:
            return False
        if wb == 0 and wp == 0:
            return False
        return True

    def _promote_rule(self, rule: dict):
        """影子 → 生效:移入 rules、重建索引、记 history、发事件。"""
        rule.get("shadow", {})["status"] = "promoted"
        self._policy.setdefault("shadow_rules", []).remove(rule)
        rule.pop("shadow", None)
        self._policy.setdefault("rules", []).append(rule)
        self._reindex_learned_actions()
        self._record_history("promote", rule["id"],
                             note=f"观察达标升迁(维度 {rule.get('trigger', {}).get('dominant_dim', '?')})")
        self.bus.emit("shadow_rule_promoted", {
            "agent_id": self.agent_id, "rule_id": rule["id"],
            "dominant_dim": rule.get("trigger", {}).get("dominant_dim", ""),
        })

    def _discard_rule(self, rule: dict, why: str):
        """影子证据不达标:标记 discarded(保留供审计回放,不再参与双轨评估)。"""
        sh = rule.get("shadow") or {}
        sh["status"] = "discarded"
        self._record_history("discard", rule.get("id", ""), note=why)
        self.bus.emit("shadow_rule_discarded", {
            "agent_id": self.agent_id, "rule_id": rule.get("id", ""),
            "reason": why,
            "observations": sh.get("observations", 0),
            "would_block": sh.get("would_block", 0),
            "would_pass": sh.get("would_pass", 0),
        })

    def rollback(self, rule_id: str) -> bool:
        """【P0-1 回滚】把一条已生效规则撤出生效集(误 promote 的后悔药)。

        规则从 rules 移回 shadow_rules 并标记 rolled_back(不再生效、保留可审计),
        重建索引 + 记 history + 发事件。找不到返回 False。"""
        for rule in list(self._policy.get("rules", [])):
            if rule.get("id") != rule_id:
                continue
            self._policy["rules"].remove(rule)
            rule["shadow"] = {
                "created_at": rule.get("created_at", ""),
                "observations": 0, "would_block": 0, "would_pass": 0,
                "status": "rolled_back",
            }
            self._policy.setdefault("shadow_rules", []).append(rule)
            self._reindex_learned_actions()
            self._record_history("rollback", rule_id, note="人工/自动回滚")
            self._save_policy()
            self.bus.emit("shadow_rule_rolled_back", {
                "agent_id": self.agent_id, "rule_id": rule_id,
            })
            return True
        return False

    def _record_history(self, event: str, rule_id: str, note: str = ""):
        """policy YAML 的 history 段(文稿第六步):propose/promote/discard/rollback/skip 全轨迹。
        有界保留最近 500 条(与库内有界日志惯例一致)。"""
        hist = self._policy.setdefault("history", [])
        hist.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event, "rule_id": rule_id,
            "version": self._policy.get("version", 1),
            "note": note[:200],
        })
        if len(hist) > 500:
            del hist[:-500]

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


    def _calibrate_thresholds(self):
        """Shadow mode 结束 — 用真实 Δ 分布校准阈值。
        关键:阈值要设在"正常任务分布"之上,这样正常任务通过,
        只有真正离群的(如完全跑题)才被拦截。
        否则用中位数附近的分位数会拦掉一半正常流量。
        """
        arr = np.array(sorted(self._delta_history), dtype=np.float64)
        if arr.size == 0:
            self._shadow_mode = False
            return
        mean = float(arr.mean())
        std = float(arr.std())
        p90 = float(np.percentile(arr, 90))
        block = max(p90, mean + 1.5 * std) * 1.10
        warn = block * 0.8
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


    def _load_policy(self) -> dict:
        if self.policy_path.exists():
            with open(self.policy_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
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
        with open(self.policy_path, "w", encoding="utf-8") as f:
            yaml.dump(self._policy, f, allow_unicode=True, sort_keys=False)
