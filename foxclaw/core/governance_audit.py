"""
治理决策统一审计(P1-2 基础 / P3-4 decision_id)。

系统的裁决点分散在四层,各说各话:
  · Harness(推理漂移:语义/工具/范围)—— emit harness_pass/warn/block
  · Permission(动作权限:deny>ask>allow 四阶段)—— 经 PolicyGate emit policy.decision.*
  · Orchestrator(跨 Agent 委派裁决)—— 原本**无任何事件**
  · SkillAdapter(执行终态:成功/失败/断路器/封杀)—— 原本**无任何事件**

本模块提供一条**统一治理审计流**,不改变任何裁决行为,只做记录:

  事件通道:脊柱广播 `governance.decision`(不在 WS 静音列表 → 前端可见)
  落盘通道:{data_dir}/audit/governance.jsonl(环形缓冲之外的持久样本)

每条记录带 **decision_id**(本次裁决的唯一 ID)与 **correlation_id**(跨层关联键):
  · permission 裁决生成 decision_id → 同一 SkillAdapter.invoke 的执行审计用它做
    correlation_id ⇒ 「谁批准的这个动作、执行结果如何」可join
  · Orchestrator 用 req.id 做 correlation_id ⇒ 多轮协商的历次裁决串成一条链

verdict 词汇(两类,查询时注意区分):
  · 裁决类:allow / warn / deny / confirm(与 PolicyGate 归一词汇一致)
  · 执行类:success / failed(SkillAdapter 的执行终态)

记录策略(防止高频正常路径淹没审计流):
  · harness / permission 层只记**非 allow**(warn/deny/confirm)——PASS 是每步推理的常态
  · orchestration 层全量(跨 Agent 委派本来就低频)
  · skill 层全量(执行审计是 P2-0 的核心价值;一次任务仅个位数调用)

与 shared_write 审计(audit_sink.py)的关系:那是「记忆写入准入」专项,这是「治理裁决」
总流;两者并存、各自落盘,互不替代。

开关:FOXCLAW_GOVERNANCE_AUDIT=0 关闭(默认开;每条 <500 字节,量级可忽略)。
审计一律 fail-safe:任何记录异常都静默吞掉,绝不影响裁决主流程(与闸门同约束)。
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import audit_chain

ENV_SWITCH = "FOXCLAW_GOVERNANCE_AUDIT"
TOPIC = "governance.decision"

VERDICTS_RULING = ("allow", "warn", "deny", "confirm")
VERDICTS_EXECUTION = ("success", "failed")

LAYERS = ("harness", "permission", "orchestration", "skill")


def new_decision_id() -> str:
    """生成一次治理裁决的唯一 ID。短 hex 够用(单机审计,非全局唯一约束)。"""
    return f"dec_{uuid.uuid4().hex[:12]}"


class GovernanceAuditor:
    """统一治理审计记录器:发脊柱事件 + 可选 JSONL 落盘。

    线程安全:记录可能来自任意 Agent 的任意线程(事件脊柱 handler 在锁外执行,
    但 invoke/route_request 本身可被并发调用)——落盘用进程内锁串行。
    多进程部署(FOXCLAW_ALLOW_INSECURE 的多 worker)不在本机开发场景内,从略。
    """

    def __init__(self, spine=None, sink_path: Path | None = None):
        self._spine = spine
        self._path = Path(sink_path) if sink_path else None
        self._lock = threading.Lock()
        self._last_hash: str | None = None

    @property
    def path(self) -> Path | None:
        return self._path

    def record(self, *, layer: str, actor: str, action: str, verdict: str,
               reason: str = "", detail: dict | None = None,
               decision_id: str | None = None,
               correlation_id: str | None = None) -> str:
        """记录一条治理裁决/执行审计,返回 decision_id(显式传入则原样返回)。

        全参数关键字化:调用点必须写明每个字段是什么,避免位置参数错位造成
        「审计记错层」这种难以察觉的污染。
        """
        did = decision_id or new_decision_id()
        rec = {
            "decision_id": did,
            "correlation_id": correlation_id or "",
            "layer": layer,
            "actor": actor,
            "action": action,
            "verdict": verdict,
            "reason": (reason or "")[:200],
            "detail": detail or {},
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self._emit(rec)
        self._persist(rec)
        return did


    def _emit(self, rec: dict) -> None:
        if self._spine is None:
            return
        try:
            self._spine.publish(TOPIC, dict(rec), source="governance_audit",
                                correlation_id=rec["correlation_id"] or None)
        except Exception:
            pass

    def _persist(self, rec: dict) -> None:
        """落盘时接哈希链(见 core/audit_chain.py):改一行/删中间一行都能被 verify 发现。

        事件通道仍发**不带链字段**的原始 rec —— 前端只关心裁决内容,而链是落盘证据;
        两个通道内容不同是有意的,别拿 WS 事件当真伪校验的依据。
        """
        if self._path is None:
            return
        try:
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                if self._last_hash is None:
                    self._last_hash = audit_chain.tail_hash(self._path)
                chained = audit_chain.link(rec, self._last_hash)
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(chained, ensure_ascii=False) + "\n")
                self._last_hash = chained["hash"]
        except Exception:
            pass

    def read(self, limit: int = 200) -> list:
        """读落盘记录(新→旧),坏行跳过。供工具/测试/未来 API 用。"""
        if self._path is None or not self._path.exists():
            return []
        out = []
        try:
            with open(self._path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            return []
        return out[-limit:][::-1]

    def verify(self) -> tuple:
        """校验落盘链 → (ok, 条数, 问题)。无落盘时视为通过。

        用法:`python -m foxclaw.core.audit_chain {data_dir}/audit/governance.jsonl`。
        """
        if self._path is None:
            return True, 0, ""
        return audit_chain.verify(self._path)


def attach(spine, data_dir) -> GovernanceAuditor | None:
    """装配工厂:FOXCLAW_GOVERNANCE_AUDIT=0 时返回 None(各层按 None 跳过记录)。

    落盘到 {data_dir}/audit/governance.jsonl(该目录已被 .gitignore 覆盖)。
    spine 为 None 时仍可落盘(standalone/测试场景),只是没有事件通道。
    """
    if os.environ.get(ENV_SWITCH, "1") == "0":
        return None
    sink = Path(data_dir) / "audit" / "governance.jsonl" if data_dir else None
    return GovernanceAuditor(spine=spine, sink_path=sink)
