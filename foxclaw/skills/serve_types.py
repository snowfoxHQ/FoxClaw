"""Serve governance result types and status mapping."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


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
