"""Harness 基础设施：contextvar 分槽 + 工具类别白名单 + 结果/基线数据类。"""
import contextvars
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..core.thought_chain import HarnessStatus


_harness_ctx: contextvars.ContextVar = contextvars.ContextVar(
    "foxclaw_harness_ctx", default=None)


def _harness_ctx_get(token: str):
    return (_harness_ctx.get() or {}).get(token)


def _harness_ctx_set(token: str, baseline):
    d = dict(_harness_ctx.get() or {})
    d[token] = baseline
    _harness_ctx.set(d)


TOOL_CATEGORIES: dict[str, set[str]] = {
    "read":    {"read_file", "search", "fetch", "list", "get", "query"},
    "write":   {"write_file", "create", "update", "save", "append"},
    "delete":  {"delete", "remove", "drop", "clear", "reset"},
    "exec":    {"run_code", "shell", "subprocess", "execute"},
    "network": {"http_get", "http_post", "scrape", "download"},
    "calendar": {"add_event", "get_schedule", "update_event"},
    "finance": {"get_balance", "add_transaction", "generate_report"},
}


@dataclass
class HarnessResult:
    status: HarnessStatus
    delta: float
    dominant_dim: str
    scores: dict
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
