"""调用级状态对象 + 任务级收集器(P0-03.1)。

替代「Router 全局降级标记」与「Agent 实例暂存字段」两种归属方式:
- Router 级布尔 `_call_degraded` 会被并发任务互相串扰(任务 A 降级、任务 B 读到);
- Agent 级字段同理——同一 Agent 实例可能并发跑多个任务。

归属改为:每次 LLM 调用一个 `CallStatus`,追加进本任务的 `TaskCallCollector`
(contextvar 注册,`asyncio.to_thread` 会复制传播),调用方从收集器读结果。
"""
import contextvars
import threading
from dataclasses import dataclass, field

# CallStatus.state 取值
PENDING = "PENDING"        # 初始态:未完成不得默认 REAL;终态必须由调用路径填充
REAL = "REAL"              # 首个候选 provider 即成功
FALLBACK = "FALLBACK"      # 主 provider 失败,回退后成功
STUB = "STUB"              # 从未配置任何模型(UNCONFIGURED),返回占位文本
DEGRADED = "DEGRADED"      # 故障降级:全部 provider 失败 / 中途流式中断 / 空响应
UNSUPPORTED = "UNSUPPORTED"  # Provider 不支持该能力(如 tools);不是失败

TERMINAL_STATES = frozenset({REAL, FALLBACK, STUB, DEGRADED, UNSUPPORTED})


@dataclass
class CallStatus:
    """一次 LLM 调用的真实状态。由 Router 填充,收集器持有引用。"""

    state: str = PENDING
    provider: str = ""          # 实际成功的 provider(失败场景留空)
    attempted: list = field(default_factory=list)   # 全部尝试过的 provider
    error: str = ""             # 最后一次失败原因
    partial: bool = False       # 流式是否已输出部分内容(中断时无法撤回)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def finish(self, state: str) -> "CallStatus":
        """置终态。调用路径返回前必须调用,残留 PENDING 视为实现 bug。"""
        self.state = state
        return self


class TaskCallCollector:
    """任务级调用状态收集器。一次 run_task 一个实例,跨线程/协程共享引用即同步。"""

    def __init__(self) -> None:
        self._items: list = []
        self._trace: list = []
        self._pending: list = []
        self._lock = threading.Lock()

    def add_trace(self, entries) -> None:
        """登记工具执行履历(requested/executed/succeeded/rejected/failed/verified)。

        工具循环在 `asyncio.to_thread` 的工作线程里跑,写回收集器(可变对象引用)后
        主线程立即可见——这也是 .3 结算能拿到 `tool_trace` 的唯一通道,
        避免再开一条「Agent 实例字段」的串扰路径。
        """
        if not entries:
            return
        with self._lock:
            self._trace.extend(entries)

    def tool_trace(self) -> list:
        with self._lock:
            return list(self._trace)

    def add_pending(self, item: dict) -> None:
        """登记一个「需用户确认后才执行」的待办动作(如 Agent 发起的桌面写操作)。

        工具循环在工作线程里跑,模型只拿到「需确认」文本;真正的确认码与参数经这里
        随任务返回体交给调用方(用户/UI),不进入模型上下文。
        """
        if item:
            with self._lock:
                self._pending.append(dict(item))

    def pending_actions(self) -> list:
        with self._lock:
            return [dict(p) for p in self._pending]

    def new(self) -> CallStatus:
        """创建并登记一个新的调用状态(初始 PENDING)。"""
        st = CallStatus()
        with self._lock:
            self._items.append(st)
        return st

    def add(self, status: "CallStatus | None") -> None:
        if status is not None:
            with self._lock:
                self._items.append(status)

    def snapshot(self) -> list:
        """返回当前登记的调用状态副本(顺序 = 发生顺序)。"""
        with self._lock:
            return list(self._items)

    def has_pending(self) -> bool:
        """是否存在未置终态的调用(用于实现 bug 断言)。"""
        return any(not s.terminal for s in self.snapshot())

    def worst_state(self) -> str:
        """最差状态(结算用):无调用 → PENDING;有 STUB/DEGRADED → 该状态;
        否则全 FALLBACK → FALLBACK;否则 REAL。"""
        states = [s.state for s in self.snapshot()]
        if not states:
            return PENDING
        for bad in (DEGRADED, STUB, PENDING):
            if bad in states:
                return bad
        return FALLBACK if FALLBACK in states else REAL

    def all_real(self) -> bool:
        snap = self.snapshot()
        return bool(snap) and all(s.state == REAL for s in snap)


# 任务级结算状态(P0-03.3)。与 resp["status"](skill_status / needs_confirmation)
# 并存但语义不同:task_status 是统一生命周期,status 是既有确认闭环字段。
COMPLETED = "COMPLETED"      # 必需业务结果 + 后置条件全满足
PARTIAL = "PARTIAL"          # 部分真实结果(工具事实在,最终解释降级)
FAILED = "FAILED"            # 无有效结果(占位/错误文本不算业务结果)
INTERRUPTED = "INTERRUPTED"  # 主动中断(stop_event)
BLOCKED = "BLOCKED"          # Harness 阻止 / 等待用户确认


def _has_business_result(result) -> bool:
    if isinstance(result, str):
        return bool(result.strip())
    return bool(result)


def _has_tool_fact(collector) -> bool:
    """工具事实:至少一步执行后核验为 verified(含断点续跑命中的 RESUMED)。

    只看「工具被调用过」不够——被拒/超时/未核验的内容不得当事实,M09 的
    「账本落账成功 + 分析未完成 → PARTIAL」正是靠这一条区分。
    """
    if collector is None:
        return False
    for step in collector.tool_trace():
        if step.get("stage") != "executed":
            continue
        for s in step.get("steps") or []:
            if str(s.get("verified") or "") in ("verified", "RESUMED"):
                return True
    return False


def _has_tool_failure(collector) -> bool:
    """工具履历里是否有明确的失败阶段(超时/异常/被拒后未补救)。

    被拒/失败的工具步不会被当成事实(见 `_has_tool_fact`),但也不该让任务被
    标成 COMPLETED——有真实模型结果时降半级为 PARTIAL,没有则 FAILED。
    """
    if collector is None:
        return False
    return any(s.get("stage") == "failed" for s in collector.tool_trace())


def settle_task_status(collector, result=None, *,
                       interrupted: bool = False, blocked: bool = False) -> str:
    """把本任务的调用状态 + 工具履历映射为统一生命周期状态(唯一结算入口)。

    冻结判定:必需业务结果 + 后置条件全满足 → COMPLETED;部分真实结果 → PARTIAL;
    无有效结果 → FAILED;主动中断 → INTERRUPTED;Harness/待确认 → BLOCKED。

    降级(STUB/DEGRADED/PENDING)不等于成功:占位文本或「全部模型失败」文本不算业务
    结果,只有同时存在**已核验的工具事实**时才降级为 PARTIAL——不靠错误字符串判成败。
    """
    if blocked:
        return BLOCKED
    if interrupted:
        return INTERRUPTED
    states = [s.state for s in (collector.snapshot() if collector is not None else [])]
    ok = _has_business_result(result)
    if not states:
        # 未经过模型调用(skill dispatch / 纯工具任务):以业务结果为准
        return COMPLETED if ok else FAILED
    worst = collector.worst_state()
    if worst in (STUB, DEGRADED, PENDING):
        return PARTIAL if (ok and _has_tool_fact(collector)) else FAILED
    if not ok:
        return FAILED
    return PARTIAL if _has_tool_failure(collector) else COMPLETED


def current_call_status() -> "CallStatus":
    """本任务的代表状态(记忆准入判定用)。无任务上下文 → REAL,不误杀单次调用。"""
    col = current_collector()
    if col is None:
        return CallStatus(state=REAL)
    return CallStatus(state=col.worst_state())


def admit_memory(*, call_status: "CallStatus | None" = None,
                 fact_evidence: dict | None = None) -> bool:
    """模型生成产物的记忆准入判定(P0-03.4 唯一入口)。

    事实通道:`fact_evidence` 非空时必须同时有「真实执行」与「验证依据」——
    被拒/超时/未核验的工具输出不得标 fact,「内容来自工具」本身不构成事实资格。
    derived 通道:STUB / DEGRADED / PENDING 的模型产物一律不准入。
    """
    if fact_evidence is not None:
        return bool(fact_evidence.get("executed") and fact_evidence.get("verified"))
    st = call_status if call_status is not None else current_call_status()
    return getattr(st, "state", PENDING) not in (STUB, DEGRADED, PENDING)


_collector: contextvars.ContextVar = contextvars.ContextVar(
    "foxclaw_task_call_collector", default=None)


def bind_collector(collector: "TaskCallCollector | None" = None):
    """在本任务上下文注册收集器。返回 (collector, token),token 用于 finally 复位。"""
    c = collector if collector is not None else TaskCallCollector()
    return c, _collector.set(c)


def current_collector() -> "TaskCallCollector | None":
    return _collector.get()


def reset_collector(token) -> None:
    _collector.reset(token)
