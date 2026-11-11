"""Behavior Spec:行为规范(硬边界)与 Persona(可调人格)分层。"""
from .spec import (
    BehaviorLoadError,
    BehaviorSpec,
    Rule,
    load,
    local_path,
    prompt_block,
    resolve,
)

__all__ = [
    "BehaviorLoadError",
    "BehaviorSpec",
    "Rule",
    "load",
    "local_path",
    "prompt_block",
    "resolve",
]
