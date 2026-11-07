"""
横切机制:断路器 + 生命周期钩子 —— AHS FRAMEWORK.md 的代码落地。

  CircuitBreaker  每个 skill 独立计数,连续失败 N 次 → OPEN → 停止重试 → 降级
  HookManager     pre_skill/post_skill/on_error/session_start/session_end +
                  agent_start(别名 pre_reasoning,可注入 additionalContext)/
                  agent_end(别名 task_complete,只读)——认知管线 run_task 触发(E2)
                  超时受限、容错、可审计;hook 是顾问不是控制器
                  (例外:pre_skill 显式返回 deny 可阻断)

对应 docs/internal/HARNESS_STANDARD.md 第二期(稳健性)。
"""
from __future__ import annotations
import time
import fnmatch
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional, Literal


# ============ 断路器 ============

CircuitState = Literal["closed", "open", "half_open"]


@dataclass
class _BreakerEntry:
    failures: int = 0                 # closed 态的连续失败计数
    state: CircuitState = "closed"
    opened_at: float = 0.0            # 进入 open 的时刻(用于冷却计时)
    probe_in_flight: bool = False     # half_open 态:是否已有探针在执行(只放一个)
    probe_started_at: float = 0.0     # 探针放行的时刻(用于探针超时自愈)
    probe_successes: int = 0          # half_open 态:已累计的探针成功数


class CircuitBreaker:
    """每个 skill 独立的断路器。标准三态机:closed / open / half_open。

    - closed:正常放行。连续失败 ≥ threshold → 转 open
    - open:停止调用。冷却 cooldown 秒后 → 转 half_open
    - half_open:只放行 ONE 个探针请求(其余仍拒绝);
        探针成功累计达 recovery_threshold → 转 closed(恢复);
        任一探针失败 → 转回 open,重新计冷却
    """

    def __init__(self, threshold: int = 3, cooldown: float = 30.0,
                 recovery_threshold: int = 1, probe_timeout: float = 60.0):
        self.threshold = threshold
        self.cooldown = cooldown
        self.recovery_threshold = max(1, recovery_threshold)  # 恢复需连续成功几次
        self.probe_timeout = probe_timeout   # 探针超过此时长未记账 → 视为丢失,释放
        self._entries: dict[str, _BreakerEntry] = {}
        self._lock = threading.RLock()    # allow/record 可能跨线程,需加锁

    def _get(self, sid: str) -> _BreakerEntry:
        if sid not in self._entries:
            self._entries[sid] = _BreakerEntry()
        return self._entries[sid]

    def allow(self, sid: str) -> bool:
        """该 skill 现在是否允许调用。

        closed → True;open(冷却中)→ False;
        open(冷却满)→ 转 half_open 并放行这一个探针;
        half_open → 仅当没有探针在飞时放行一个,否则 False。
        """
        with self._lock:
            e = self._get(sid)
            if e.state == "closed":
                return True
            if e.state == "open":
                if time.time() - e.opened_at >= self.cooldown:
                    # 冷却满 → 真正进入 half_open,放行第一个探针
                    e.state = "half_open"
                    e.probe_in_flight = True
                    e.probe_started_at = time.time()
                    e.probe_successes = 0
                    return True
                return False
            # half_open:已有探针在飞 → 拒绝其余;否则放行一个新探针
            if e.state == "half_open":
                if e.probe_in_flight:
                    # 探针超时未记账(调用方崩溃/漏记)→ 视为丢失,释放以免永久卡死
                    if time.time() - e.probe_started_at >= self.probe_timeout:
                        e.probe_in_flight = False
                    else:
                        return False
                e.probe_in_flight = True
                e.probe_started_at = time.time()
                return True
            return False

    def record_success(self, sid: str):
        with self._lock:
            e = self._get(sid)
            if e.state == "half_open":
                e.probe_in_flight = False
                e.probe_successes += 1
                if e.probe_successes >= self.recovery_threshold:
                    # 探针连续成功达标 → 恢复
                    e.state = "closed"
                    e.failures = 0
                    e.probe_successes = 0
                # 否则保持 half_open,等下一个探针
            else:
                e.failures = 0
                e.state = "closed"

    def record_failure(self, sid: str):
        with self._lock:
            e = self._get(sid)
            if e.state == "half_open":
                # 探针失败 → 回到 open,重新计冷却
                e.state = "open"
                e.opened_at = time.time()
                e.probe_in_flight = False
                e.probe_successes = 0
                return
            e.failures += 1
            if e.failures >= self.threshold:
                e.state = "open"
                e.opened_at = time.time()

    def status(self, sid: str) -> dict:
        with self._lock:
            e = self._get(sid)
            state = e.state
            # open 但冷却已满 → 对外显示 half_open(下次 allow 会真正转入)
            if e.state == "open" and time.time() - e.opened_at >= self.cooldown:
                state = "half_open"
            return {"state": state, "failures": e.failures,
                    "threshold": self.threshold,
                    "probe_successes": e.probe_successes,
                    "recovery_threshold": self.recovery_threshold}

    def is_open(self, sid: str) -> bool:
        """只读查询:该 skill 当前是否处于「阻止调用」状态。

        【P4-0】原实现写的是 `state == "open" and not self.allow(sid)` —— 但 allow() 在
        「open 且冷却已满」时会**副作用性地**转入 half_open 并占掉唯一的探针名额。于是
        「只想看一眼断路器开着没」的监控代码会意外消耗掉恢复探测机会,把真正该放行的探针
        挡在外面。这里改成纯读,不做任何状态迁移。

        语义:open 且冷却未满 → 阻止;open 且冷却已满 → 视作「下次调用会被放行」,不计为打开;
        half_open 仅当探针在飞且未超时时计入。
        """
        with self._lock:
            e = self._get(sid)
            if e.state == "open":
                return (time.time() - e.opened_at) < self.cooldown
            if e.state == "half_open":
                return e.probe_in_flight and (time.time() - e.probe_started_at) < self.probe_timeout
            return False


# ============ 生命周期钩子 ============

HookEvent = Literal["before_tool_call", "after_tool_call", "on_error", "on_loop",
                    "session_start", "agent_start", "agent_end", "session_end",
                    "before_message_write",
                    # 向后兼容的旧名 + E2 语义别名(内部规整为标准名)
                    "pre_skill", "post_skill", "pre_reasoning", "task_complete"]


@dataclass
class Hook:
    event: HookEvent
    matcher: str                 # skill id / glob;'*' 匹配全部
    handler: Callable            # 宿主提供的处理函数
    name: str = ""

    def matches(self, skill_id: str) -> bool:
        return fnmatch.fnmatch(skill_id or "", self.matcher)


@dataclass
class HookResult:
    blocked: bool = False        # 仅 pre_skill 可阻断
    reason: str = ""
    modified_args: Optional[dict] = None
    additional_context: Optional[str] = None   # E2:CONTEXT_HOOKS(pre_reasoning/agent_start)注入的推理上下文


class HookManager:
    """生命周期钩子管理。钩子超时受限、容错、可审计。

    规则(对齐 FRAMEWORK.md):
    - 超时受限:单个 hook 超过 timeout 视为出错(不阻塞链路)
    - 容错:hook 失败不中断 skill 链(顾问不是控制器)
    - 例外:pre_skill 显式返回 {block:True} 可阻断(这是它被授予的权力)
    - 可审计:每次 hook 执行记录到 audit_log
    """

    def __init__(self, timeout: float = 10.0, audit_limit: int = 2000):
        self.timeout = timeout
        self._hooks: list[Hook] = []
        # 【深审修复·长期运行泄漏】audit_log 原为普通 list、只增不减:每次 skill invoke 触发
        # pre/post/error/loop 等钩子都 append,长跑(尤其 V2 管线内 dispatch 高频用工具)无界
        # 增长 → 内存泄漏(与 TruthCrystallizer._pending 同类)。改有界 deque:保留最近 N 条,
        # 溢出自动丢弃最老的;deque.append 原子 ⇒ 并发 fire 也线程安全,无需额外锁。
        self.audit_log: deque = deque(maxlen=max(100, audit_limit))

    def register(self, event: HookEvent, handler: Callable,
                 matcher: str = "*", name: str = ""):
        from .hooks_spec import normalize_hook, is_valid_hook
        norm = normalize_hook(event)   # 旧名(pre_skill)→ 标准名(before_tool_call)
        if not is_valid_hook(norm):
            # 非法钩子名(可能拼写错误)→ 记录警告,避免静默失效难以排查
            self.audit_log.append({
                "event": event, "hook": name or getattr(handler, "__name__", "?"),
                "matcher": matcher, "duration_ms": 0, "timed_out": False,
                "error": f"未知钩子点「{event}」,该钩子永不触发(请检查拼写)",
            })
        self._hooks.append(Hook(norm, matcher, handler, name or handler.__name__))

    def _run_one(self, hook: Hook, ctx: dict) -> dict:
        """执行单个 hook,计时 + 容错。返回 hook 的返回值(dict)或 {}。"""
        t0 = time.time()
        outcome, err = {}, None
        try:
            ret = hook.handler(ctx)
            if isinstance(ret, dict):
                outcome = ret
        except Exception as e:
            err = str(e)
        dt = time.time() - t0
        # 超时只记录(同步 hook 无法真正中断,但记录超时供审计)
        timed_out = dt > self.timeout
        self.audit_log.append({
            "event": hook.event, "hook": hook.name, "matcher": hook.matcher,
            "duration_ms": round(dt * 1000, 1), "timed_out": timed_out,
            "error": err,
        })
        return {} if err else outcome

    def fire(self, event: HookEvent, skill_id: str = "", ctx: dict = None) -> HookResult:
        """触发某事件的所有匹配 hook。仅 before_tool_call(原 pre_skill)的 block 生效;
        CONTEXT_HOOKS(pre_reasoning/agent_start)返回的 additional_context 会被收集拼接。

        【P4-3】⚠ ctx 是**浅拷贝**(dict(...)),不是深拷贝。hook 不得原地修改 ctx 里的嵌套
        dict(如 ctx["args"]["x"])——那会连带改掉调用方的原对象。要改参数请用返回值
        `{"modified_args": {...}}` 整块替换(仅 before_tool_call 生效)。此处刻意不做 deepcopy,
        以免每次工具调用都付深拷贝开销;约束靠本注释 + 钩子约定维持。
        """
        from .hooks_spec import (normalize_hook, BLOCKING_HOOKS, TOOL_SCOPED_HOOKS,
                                 CONTEXT_HOOKS)
        event = normalize_hook(event)   # 旧名/标准名统一
        ctx = dict(ctx or {}, event=event, skill_id=skill_id)
        result = HookResult()
        ctx_parts = []
        for hook in self._hooks:
            if hook.event != event:
                continue
            # 工具级钩子按 skill_id 过滤(matcher)
            if event in TOOL_SCOPED_HOOKS and not hook.matches(skill_id):
                continue
            out = self._run_one(hook, ctx)
            # 仅有阻断权的钩子(before_tool_call)的 block / modified_args 生效
            if event in BLOCKING_HOOKS:
                if out.get("block"):
                    result.blocked = True
                    result.reason = out.get("reason", f"被 {event} 钩子阻断")
                    return result
                if out.get("modified_args") is not None:
                    result.modified_args = out["modified_args"]
            # 可注入上下文的钩子:收集 additional_context(兼容驼峰 additionalContext)
            if event in CONTEXT_HOOKS:
                inj = out.get("additional_context") or out.get("additionalContext")
                if inj:
                    ctx_parts.append(str(inj))
        if ctx_parts:
            result.additional_context = "\n".join(ctx_parts)
        return result

    def recent_audit(self, n: int = 20) -> list:
        # deque 不支持负索引切片,先快照成 list(原子快照,并发安全)再取尾 N 条
        return list(self.audit_log)[-n:]
