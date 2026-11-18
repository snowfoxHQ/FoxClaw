"""记忆准入统一守卫(P0-03.4)回归。

覆盖 M07/M08/M10:
  · derived 通道:REAL/FALLBACK 的模型产物准入,STUB/DEGRADED/PENDING 一律拦下;
  · fact 通道:必须同时有「真实执行」与「验证依据」,仅「来自工具」不构成资格;
  · 真实调用点:财务记账走 fact(落账回执),财务分析走 derived(受守卫)。
"""
from __future__ import annotations

import asyncio
import types

from foxclaw.core import call_status as cs
from foxclaw.core.call_status import CallStatus, admit_memory, bind_collector, reset_collector


def _st(state: str) -> CallStatus:
    return CallStatus(state=state)


def test_derived_admitted_only_when_real():
    assert admit_memory(call_status=_st(cs.REAL)) is True
    assert admit_memory(call_status=_st(cs.FALLBACK)) is True


def test_derived_blocked_on_stub_degraded_pending():
    assert admit_memory(call_status=_st(cs.STUB)) is False
    assert admit_memory(call_status=_st(cs.DEGRADED)) is False
    assert admit_memory(call_status=_st(cs.PENDING)) is False


def test_fact_channel_requires_executed_and_verified():
    assert admit_memory(fact_evidence={"executed": True, "verified": True}) is True
    assert admit_memory(fact_evidence={"executed": True}) is False
    assert admit_memory(fact_evidence={"verified": True}) is False
    assert admit_memory(fact_evidence={"executed": True, "verified": False}) is False
    assert admit_memory(fact_evidence={}) is False


def test_fact_channel_ignores_call_status():
    """工具被拒/超时不会让事实通道过关,模型降级也不影响真实落账。"""
    assert admit_memory(call_status=_st(cs.DEGRADED),
                        fact_evidence={"executed": True, "verified": True}) is True
    assert admit_memory(call_status=_st(cs.REAL),
                        fact_evidence={"executed": True, "verified": False}) is False


def test_admit_reads_task_collector():
    col, tok = bind_collector()
    try:
        assert admit_memory() is False         # 任务内还没有调用 → 保守拦下
        col.add(_st(cs.REAL))
        assert admit_memory() is True          # 真实调用 → 放行
        col.add(_st(cs.DEGRADED))
        assert admit_memory() is False         # 任务已降级 → 拦下
    finally:
        reset_collector(tok)
    assert admit_memory() is True              # 任务结束(无收集器)→ 恢复宽松


class _Fin:
    def __init__(self, rec):
        self.rec = rec

    def record_text(self, task):
        return self.rec


class _FakeFinAgent:
    def __init__(self, rec):
        self.fin = rec
        self.stored: list = []

    def _think(self, **kw):
        pass

    def _store_memory(self, **kw):
        self.stored.append(kw)


def _auto_record(agent, task):
    from foxclaw.agents.impl.finance import FinanceAgent
    return FinanceAgent._auto_record(agent, task)


def test_ledger_write_uses_fact_channel():
    rec = {"recorded": [{"amount": -105.0}], "ack": "已记账 1 笔", "dry_run": False}
    a = _FakeFinAgent(_Fin(rec))
    assert _auto_record(a, "今天买了一件衣服花了105块") == "已记账 1 笔"
    assert len(a.stored) == 1, "真实落账 → fact 通道放行"


def test_ledger_dry_run_not_admitted():
    rec = {"recorded": [{"amount": -105.0}], "ack": "预览(未落账) 1 笔", "dry_run": True}
    a = _FakeFinAgent(_Fin(rec))
    _auto_record(a, "今天买了一件衣服花了105块")
    assert a.stored == [], "未落账的预览不是事实,不得写入记忆"


def test_ledger_empty_not_admitted():
    a = _FakeFinAgent(_Fin({"recorded": [], "ack": "没听出金额", "dry_run": False}))
    _auto_record(a, "随便聊聊")
    assert a.stored == []


def test_finance_analysis_blocked_when_degraded():
    from foxclaw.agents.impl.finance import FinanceAgent

    stored: list = []

    async def _call(*a, **k):
        return "分析结论"

    a = types.SimpleNamespace(
        fin=None,
        _auto_record=lambda t: "",
        _call_llm_web=_call,
        _think=lambda **kw: None,
        _store_memory=lambda **kw: stored.append(kw),
    )
    col, tok = bind_collector()
    try:
        col.add(_st(cs.DEGRADED))
        asyncio.run(FinanceAgent._execute(a, "看看这个月花销", {}, "s1", []))
    finally:
        reset_collector(tok)
    assert stored == [], "降级任务的模型分析结论不准入记忆"
