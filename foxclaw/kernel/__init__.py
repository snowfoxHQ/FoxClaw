"""Foxclaw 内核层:进程管理、调度、事件脊柱、能力注册等 OS 式抽象。"""
from .process_manager import ProcessManager, ProcessRecord
from .spine import EventSpine, Event
from .capabilities import CapabilityRegistry
from .scheduler import Scheduler
from .policy_gate import PolicyGate, Verdict

__all__ = [
    "ProcessManager", "ProcessRecord",
    "EventSpine", "Event",
    "CapabilityRegistry", "Scheduler",
    "PolicyGate", "Verdict",
]
