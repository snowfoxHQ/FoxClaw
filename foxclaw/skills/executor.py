"""Execution routing between FoxClaw's local tools and Serve governance MCP."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse


class ServeCategory(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NOT_FOUND = "not_found"
    EXPIRED = "expired"
    CONFLICT = "conflict"
    INVALID = "invalid"
    INTEGRITY_FAILURE = "integrity_failure"
    EXECUTION_UNCERTAIN = "execution_uncertain"
    EXECUTED = "executed"
    UNAUTHENTICATED = "unauthenticated"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


_STATUS_CATEGORIES = {
    "pending": ServeCategory.ACCEPTED,
    "deduped": ServeCategory.ACCEPTED,
    "rejected": ServeCategory.REJECTED,
    "rejected_unaudited": ServeCategory.REJECTED,
    "not_found": ServeCategory.NOT_FOUND,
    "expired": ServeCategory.EXPIRED,
    "idempotency_conflict": ServeCategory.CONFLICT,
    "args_conflict": ServeCategory.CONFLICT,
    "unknown_arg": ServeCategory.INVALID,
    "args_invalid": ServeCategory.INVALID,
    "invalid_decision": ServeCategory.INVALID,
    "unauthenticated": ServeCategory.UNAUTHENTICATED,
    "integrity_error": ServeCategory.INTEGRITY_FAILURE,
    "executed_unaudited": ServeCategory.INTEGRITY_FAILURE,
    "executed_state_unpersisted": ServeCategory.INTEGRITY_FAILURE,
    "execution_state_unpersisted": ServeCategory.INTEGRITY_FAILURE,
    "execution_unknown": ServeCategory.EXECUTION_UNCERTAIN,
    "executing": ServeCategory.EXECUTION_UNCERTAIN,
    "pending_missing": ServeCategory.EXECUTION_UNCERTAIN,
    "not_executed": ServeCategory.REJECTED,
    "draft": ServeCategory.ACCEPTED,
    "clarifying": ServeCategory.ACCEPTED,
    "clarified": ServeCategory.ACCEPTED,
    "bound": ServeCategory.ACCEPTED,
    "planned": ServeCategory.ACCEPTED,
    "approved": ServeCategory.ACCEPTED,
    "halted": ServeCategory.REJECTED,
    "verified": ServeCategory.EXECUTED,
    "succeeded": ServeCategory.EXECUTED,
    "failed": ServeCategory.REJECTED,
    "rolled_back": ServeCategory.REJECTED,
    "aborted": ServeCategory.REJECTED,
    "ok": ServeCategory.EXECUTED,
}


@dataclass(frozen=True)
class ServeResult:
    status: str
    category: ServeCategory
    data: dict = field(default_factory=dict)
    message: str = ""
    idempotency_key: str = ""

    @property
    def contract_id(self) -> str:
        return str(self.data.get("contract_id") or "")


@dataclass(frozen=True)
class RouteDecision:
    backend: Literal["local", "serve", "blocked"]
    message: str = ""
    degraded: bool = False


class GovernanceExecutor:
    """Single routing point and thin client for the five Serve governance tools."""

    def __init__(self, backend: str | None = None, *, serve_url: str | None = None,
                 token: str | None = None, token_file: str | None = None,
                 health_interval: float | None = None, client=None,
                 start_monitor: bool = True):
        selected = (backend or os.environ.get("FOXCLAW_EXECUTION_BACKEND", "local")).strip().lower()
        if selected not in {"local", "serve", "auto"}:
            raise ValueError("FOXCLAW_EXECUTION_BACKEND 只能是 local、serve 或 auto")
        self.execution_backend: Literal["local", "serve", "auto"] = selected
        self.serve_url = (serve_url if serve_url is not None else
                          os.environ.get("FOXCLAW_SERVE_URL", "")).strip()
        raw_token = token if token is not None else os.environ.get("FOXCLAW_SERVE_TOKEN", "")
        self._token_file = (token_file if token_file is not None else
                            os.environ.get("FOXCLAW_SERVE_TOKEN_FILE", "")).strip()
        if not raw_token and self._token_file:
            raw_token = Path(self._token_file).expanduser().read_text(encoding="utf-8").strip()
        self._token = raw_token.strip()
        self._client = client
        interval = health_interval if health_interval is not None else float(
            os.environ.get("FOXCLAW_SERVE_HEALTH_INTERVAL", "").strip() or "30")
        if interval <= 0:
            raise ValueError("FOXCLAW_SERVE_HEALTH_INTERVAL 必须大于 0")
        self.health_interval = interval
        parsed = urlparse(self.serve_url)
        if self.serve_url and (parsed.scheme not in ("http", "https") or not parsed.hostname
                               or parsed.username or parsed.password):
            raise ValueError("FOXCLAW_SERVE_URL 必须是无内嵌凭证的 http(s) URL")
        self._health_lock = threading.RLock()
        self._health = {"status": "unreachable", "checked_at": None, "reason": "未探测"}
        self._alert = None
        self._stop = threading.Event()
        self._thread = None
        self._known_contracts: dict[str, str] = {}
        self._uncertain_keys: set[str] = set()
        self._known_lock = threading.Lock()
        if start_monitor and self.serve_url and self._token:
            self._thread = threading.Thread(target=self._health_loop, daemon=True,
                                            name="serve-health")
            self._thread.start()
        elif not self.serve_url:
            self._health["reason"] = "未配置 FOXCLAW_SERVE_URL"
        elif not self._token:
            self._health["reason"] = "未配置 Serve bearer token"

    @staticmethod
    def _category(status: str) -> ServeCategory:
        return _STATUS_CATEGORIES.get(status, ServeCategory.UNKNOWN)

    @property
    def health(self) -> dict:
        with self._health_lock:
            return dict(self._health)

    @property
    def recent_degradation(self) -> dict | None:
        with self._health_lock:
            return dict(self._alert) if self._alert else None

    def close(self) -> None:
        self._stop.set()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    def _call(self, tool: str, args: dict) -> dict:
        if self._client is not None:
            out = self._client.call_tool(tool, args)
        else:
            from .mcp_client import call_streamable_http_tool
            out = call_streamable_http_tool(self.serve_url, self._headers(), tool, args,
                                            timeout=10.0)
        if out.get("isError"):
            raise RuntimeError("MCP tool returned an error")
        structured = out.get("structured")
        if isinstance(structured, dict):
            return structured
        content = out.get("content", "")
        try:
            decoded = json.loads(content)
        except (TypeError, ValueError):
            raise RuntimeError("MCP tool response was not a structured object") from None
        if not isinstance(decoded, dict):
            raise RuntimeError("MCP tool response was not an object")
        return decoded

    def probe(self) -> dict:
        if not self.serve_url or not self._token:
            state = "unreachable"
            reason = "Serve 地址或 bearer token 未配置"
        else:
            try:
                data = self._call("serve_health", {})
                state = data.get("status")
                if state not in ("ok", "degraded"):
                    state = "unreachable"
                    reason = "Serve health 返回未知状态"
                else:
                    reason = ""
            except Exception as exc:
                state = "unreachable"
                reason = type(exc).__name__
        snapshot = {"status": state, "checked_at": time.time(), "reason": reason}
        with self._health_lock:
            previous = self._health.get("status")
            self._health = snapshot
            if state != "ok":
                self._alert = {"at": snapshot["checked_at"], "status": state,
                               "reason": reason or "Serve health degraded"}
            elif previous in ("degraded", "unreachable"):
                self._alert = {"at": snapshot["checked_at"], "status": "recovered",
                               "reason": "Serve health recovered"}
        return dict(snapshot)

    def _health_loop(self) -> None:
        while not self._stop.is_set():
            self.probe()
            self._stop.wait(self.health_interval)

    def route(self, skill) -> RouteDecision:
        """Resolve requires_audit > skill override > global default."""
        global_mode = self.execution_backend
        configured = getattr(skill, "execution_backend", None) or global_mode
        if configured not in {"local", "serve", "auto"}:
            return RouteDecision("blocked", "技能 execution_backend 配置无效")
        if getattr(skill, "requires_audit", False):
            if global_mode == "local":
                return RouteDecision("blocked", "此操作要求企业审计；当前为 local 模式，请切换到 serve")
            configured = "serve"
        if configured == "local":
            return RouteDecision("local")
        health = self.health["status"]
        if configured == "serve":
            if health != "ok":
                return RouteDecision("blocked", f"Serve 健康状态为 {health}，受治理写操作已 fail-closed")
            return RouteDecision("serve")
        if health == "ok":
            return RouteDecision("serve")
        if getattr(skill, "requires_audit", False):
            return RouteDecision("blocked", f"此操作要求企业审计；Serve 状态为 {health}，拒绝本地降级")
        self._record_degradation(getattr(skill, "id", ""), health)
        return RouteDecision("local", f"Serve 状态为 {health}，已按 auto 规则降级本地执行", True)

    def _record_degradation(self, skill_id: str, status: str) -> None:
        alert = {"at": time.time(), "status": status, "skill_id": skill_id,
                 "message": "auto 模式因 Serve 不可用而降级到本地执行"}
        with self._health_lock:
            self._alert = alert

    @staticmethod
    def _operation_args(skill, args: dict) -> dict:
        """Allow only declared operation parameters; exclude task/memory/context payloads."""
        if getattr(skill, "arg_passthrough", False):
            # desktop/browser 等边界动作:参数即操作本体,且已按完整参数在本地边界完成授权;
            # Serve 契约必须携带同一参数。声明式白名单只用于技能参数(剥离 context 载荷)。
            return dict(args or {})
        params = getattr(skill, "params", None) or {}
        properties = params.get("properties") if isinstance(params, dict) else None
        allowed = set(properties if isinstance(properties, dict) else params)
        return {key: value for key, value in (args or {}).items() if key in allowed}

    def submit(self, skill, args: dict, idempotency_key: str | None = None) -> ServeResult:
        key = idempotency_key or uuid.uuid4().hex
        intent = str(getattr(skill, "id", ""))
        operation_args = self._operation_args(skill, args)
        with self._known_lock:
            known_contract_id = self._known_contracts.get(key)
            previously_uncertain = key in self._uncertain_keys
        if previously_uncertain and not known_contract_id:
            return ServeResult(
                "unknown_outcome", ServeCategory.UNAVAILABLE,
                message="上次提交结果不确定且没有契约号可查询；未再次提交",
                idempotency_key=key)
        if known_contract_id:
            try:
                previous = self.query_contract(known_contract_id)
                if previous.status != "expired":
                    return ServeResult(previous.status, previous.category, previous.data,
                                       previous.message, key)
                key = uuid.uuid4().hex
            except Exception as exc:
                with self._known_lock:
                    self._uncertain_keys.add(key)
                return ServeResult(
                    "unknown_outcome", ServeCategory.UNAVAILABLE,
                    message=f"Serve 契约查询失败({type(exc).__name__})；未重试提交",
                    idempotency_key=key)
        try:
            data = self._call("serve_submit_action", {
                "action_intent": intent,
                "args": operation_args,
                "idempotency_key": key,
            })
        except Exception as exc:
            with self._known_lock:
                contract_id = self._known_contracts.get(key)
            if contract_id:
                try:
                    queried = self.query_contract(contract_id)
                    return ServeResult(queried.status, queried.category, queried.data,
                                       queried.message, key)
                except Exception:
                    pass
            else:
                with self._known_lock:
                    self._uncertain_keys.add(key)
            return ServeResult("unknown_outcome", ServeCategory.UNAVAILABLE,
                               message=f"Serve 请求结果不确定({type(exc).__name__})；未自动重试",
                               idempotency_key=key)
        status = str(data.get("status") or "unknown")
        if status == "expired":
            key = uuid.uuid4().hex
            try:
                data = self._call("serve_submit_action", {
                    "action_intent": intent, "args": operation_args,
                    "idempotency_key": key,
                })
                status = str(data.get("status") or "unknown")
            except Exception as exc:
                return ServeResult("unknown_outcome", ServeCategory.UNAVAILABLE,
                                   message=f"过期契约重新提交结果不确定({type(exc).__name__})；未重试",
                                   idempotency_key=key)
        contract_id = str(data.get("contract_id") or "")
        with self._known_lock:
            self._uncertain_keys.discard(key)
            if contract_id:
                self._known_contracts[key] = contract_id
        return ServeResult(status, self._category(status), data,
                           str(data.get("reason") or ""), key)

    def query_contract(self, contract_id: str) -> ServeResult:
        data = self._call("serve_query_contract", {"contract_id": contract_id})
        status = str(data.get("status") or "unknown")
        return ServeResult(status, self._category(status), data,
                           str(data.get("reason") or ""))

    def submit_action(self, spec, args: dict, decision_id: str = "") -> dict:
        """desktop/browser 边界共用的 Serve 提交归一化。

        与 SkillAdapter 的 serve 分支同一语义:accepted+requires_approval → needs_approval
        (确认由 Serve 契约审批承担,不走本地 nonce);accepted → submitted;其余一律 blocked。
        授权语义不随执行去处改变;decision_id 随响应回传以便审计关联。
        """
        submitted = self.submit(spec, args)
        if submitted.category.value == "accepted":
            requires_approval = bool(submitted.data.get("requires_approval"))
            status = "needs_approval" if requires_approval else "submitted"
            message = (f"已提交审批（契约号：{submitted.contract_id}）" if requires_approval
                       else f"已提交 Serve 契约（契约号：{submitted.contract_id}），等待服务端确认")
            return {"ok": False, "denied": False, "status": status, "level": status,
                    "message": message, "execution_backend": "serve",
                    "contract_id": submitted.contract_id,
                    "idempotency_key": submitted.idempotency_key,
                    "decision_id": decision_id, "data": submitted.data}
        return {"ok": False, "denied": True,
                "reason": submitted.message or f"Serve 契约状态：{submitted.status}",
                "level": "blocked", "execution_backend": "blocked",
                "contract_id": submitted.contract_id,
                "idempotency_key": submitted.idempotency_key,
                "decision_id": decision_id}

    def confirm_action(self, contract_id: str, decision: str) -> ServeResult:
        data = self._call("serve_confirm_action", {
            "contract_id": contract_id, "decision": decision,
        })
        status = str(data.get("status") or "unknown")
        return ServeResult(status, self._category(status), data,
                           str(data.get("reason") or ""))

    def query_audit(self, contract_id: str) -> ServeResult:
        data = self._call("serve_query_audit", {"contract_id": contract_id})
        status = str(data.get("status") or "unknown")
        return ServeResult(status, self._category(status), data,
                           str(data.get("reason") or ""))
