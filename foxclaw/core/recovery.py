"""
Foxclaw Core — 崩溃/重启恢复 (Restart Recovery)

借鉴 OpenClaw 的原理,适配 Foxclaw。核心思想:
  系统跑长任务到一半崩溃/重启,那个任务不该凭空消失。
  启动时主动识别"上次还在进行、但现在没有活跃进程持有"的任务,
  诚实标记为"被中断",而不是装无事或留下半完成的脏状态。

这与 Foxclaw"可审计、可信"的定位高度契合——可信系统不该悄悄吞掉崩溃的任务。

机制:
  · 任务开始 → 落一条 in_progress 记录(带 pid + 启动时间)
  · 任务正常结束 → 标记 done(或从活跃集移除)
  · 启动时 → 扫描所有 in_progress 记录:
      - 若记录的 pid 不是当前进程、且那个 pid 已不存在 → 判定被中断
      - 标记为 interrupted,交给上层决定(通知用户 / 尝试恢复)

存储:JSONL 追加写(崩溃安全:写一条是原子 append,不会损坏整个文件)。
"""
from __future__ import annotations
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict


def _pid_alive(pid: int) -> bool:
    """判断进程是否存活(只查询、绝不影响目标进程)。跨平台:

    · POSIX:os.kill(pid, 0) —— 发 0 号信号,内核只做权限/存在性检查,不真正投递。
    · Windows:**决不能**用 os.kill(pid, 0)。CPython 在 Windows 上把非 CTRL 信号
      翻译成 TerminateProcess(handle, sig),因此 os.kill(pid, 0) 会真的以退出码 0
      终止该 pid(已实测:会误杀 pid 被 OS 复用后的无辜进程,且 os.kill 正常返回、
      不抛异常,连"探测失败"都察觉不到)。改用 OpenProcess 只申请查询权限、绝不
      终止,再用 GetExitCodeProcess 看是否仍 STILL_ACTIVE(259)。

    非破坏性是硬约束:本函数只在启动恢复扫描里做诊断记录,任何情况下都不得
    对被探测进程产生副作用。
    """
    if pid <= 0:
        return False

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False       # 打不开句柄 = 进程不存在(或无权限)→ 保守判不存活
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            # STILL_ACTIVE=259 表示仍在运行。极少数进程恰以 259 退出会被误判存活,
            # 但这是"只读探测"的可接受代价——绝不误杀才是首要目标(psutil 同此取舍)。
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    # POSIX
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True       # 存在但无权限 → 视为存活
    except Exception:
        return False


@dataclass
class TaskRecord:
    task_id: str
    description: str
    pid: int
    started: float
    status: str = "in_progress"        # in_progress / done / failed / interrupted
    session_id: str = ""
    channel: str = ""
    finished: float = 0.0
    boot_id: str = ""                  # 哪次启动产生的(防 pid 复用误判)
    # 【P2-4】恢复策略字段
    retryable: bool = False            # 调用方标记"可安全重试"(无副作用/幂等)
    start_count: int = 1               # 同一 task_id 被 start 的次数(重复 start 保留最后一次)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RecoveryPlan:
    """【P2-4】恢复计划:把中断任务分类,调用方(API 层/前端)据此决定动作。"""
    retryable: list = field(default_factory=list)      # 可重试的 task_id
    notify_user: list = field(default_factory=list)    # 需通知用户的(来自某个渠道)
    needs_manual: list = field(default_factory=list)   # 需人工处理的(保守默认)
    details: list = field(default_factory=list)        # [{task_id,description,channel,...,action}]

    def to_dict(self) -> dict:
        return asdict(self)


class RestartRecovery:
    """任务生命周期日志 + 启动恢复扫描。"""

    def __init__(self, log_path: str, current_pid: int = None, boot_id: str = None,
                 spine=None, fsync: bool = True):
        self.log_path = log_path
        self.current_pid = current_pid or os.getpid()
        # boot_id:每次启动唯一。任务记录带上它,扫描时"非本次启动的 in_progress"
        # 即属于上一次运行 —— 比单纯靠 pid 可靠(pid 会被 OS 复用)。
        import uuid
        self.boot_id = boot_id or uuid.uuid4().hex[:16]
        self._active: dict[str, TaskRecord] = {}   # 当前进程内进行中的任务
        self._spine = spine                        # 【P2-4】审计脊柱(可空,None 静默)
        self._fsync = fsync                        # 【P2-4】fsync 可配(默认开=崩溃安全;高频场景可关)
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)

    # ── 任务生命周期 ──────────────────────────────────────
    def task_started(self, task_id: str, description: str,
                     session_id: str = "", channel: str = "",
                     retryable: bool = False) -> TaskRecord:
        rec = TaskRecord(
            task_id=task_id, description=description[:200],
            pid=self.current_pid, started=time.time(),
            session_id=session_id, channel=channel, boot_id=self.boot_id,
            retryable=retryable)
        self._active[task_id] = rec
        self._append({"event": "start", **rec.to_dict()})
        return rec

    def task_finished(self, task_id: str, status: str = "done"):
        rec = self._active.pop(task_id, None)
        if rec:
            rec.status = status
            rec.finished = time.time()
        self._append({"event": "finish", "task_id": task_id,
                      "status": status, "pid": self.current_pid,
                      "finished": time.time()})

    # ── 步骤级 checkpoint(P1 断点可续)─────────────────────
    def record_step(self, task_id: str, step: dict) -> None:
        """记一条「已完成的步骤」。管线每完成一个 stage、工具轮每完成一个工具调用各落一条,
        崩溃/断连后 `steps_of()` 能回答「跑到哪一步」,也让续跑知道哪些步骤不必重做。

        fail-safe:写日志失败绝不影响任务执行(与任务生命周期记账同一定位)。
        """
        if not task_id or not isinstance(step, dict):
            return
        self._append({"event": "step", "task_id": task_id,
                      "ts": time.time(), **step})

    def steps_of(self, task_id: str, limit: int = 200) -> list:
        """回放日志,取该 task 已记录的步骤(写入顺序;只留最近 limit 条)。

        只读、容错:文件不存在/行损坏/中途写一半都跳过,不抛异常。
        """
        out: list = []
        if not task_id or not os.path.exists(self.log_path):
            return out
        try:
            with open(self.log_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue        # 末行可能写一半(append 崩溃),跳过
                    if ev.get("event") == "step" and ev.get("task_id") == task_id:
                        out.append({k: v for k, v in ev.items()
                                    if k not in ("event", "task_id")})
        except Exception:
            return out
        return out[-limit:] if limit else out

    # ── 启动恢复扫描 ──────────────────────────────────────
    def scan_interrupted(self) -> list[TaskRecord]:
        """启动时调用:重建日志状态,找出被中断的任务。

        判定(双重,避免 pid 复用误判):
          1. 任务仍是 in_progress
          2. 它的 boot_id 不是本次启动(即属于上一次运行)
        boot_id 随机不复用,所以"非本次启动的进行中任务"必然是被中断的。
        """
        records = self._replay()
        interrupted = []
        for task_id, rec in records.items():
            if rec.status != "in_progress":
                continue
            if rec.boot_id == self.boot_id:
                continue        # 本次启动自己的任务,跳过
            # 非本次启动的 in_progress = 上次运行遗留 = 被中断
            # (pid 是否存活作为辅助信息记录,但不作为唯一判据)
            rec.status = "interrupted"
            interrupted.append(rec)
            self._append({"event": "interrupted", "task_id": task_id,
                          "pid": rec.pid, "prev_boot_id": rec.boot_id,
                          "pid_still_alive": _pid_alive(rec.pid),
                          "detected_by": self.boot_id,
                          "detected_at": time.time()})
        return interrupted

    def _replay(self) -> dict[str, TaskRecord]:
        """回放日志,重建每个 task 的最终状态。"""
        records: dict[str, TaskRecord] = {}
        if not os.path.exists(self.log_path):
            return records
        try:
            with open(self.log_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue        # 跳过损坏行(append 模式下末行可能写一半)
                    self._apply_event(records, ev)
        except Exception:
            pass
        return records

    @staticmethod
    def _apply_event(records: dict, ev: dict):
        tid = ev.get("task_id")
        if not tid:
            return
        kind = ev.get("event")
        if kind == "start":
            # 【P2-4】同一 task_id 多次 start:保留最后一次,但累计 start_count
            existing = records.get(tid)
            start_count = (existing.start_count + 1) if existing else 1
            records[tid] = TaskRecord(
                task_id=tid, description=ev.get("description", ""),
                pid=ev.get("pid", 0), started=ev.get("started", 0),
                status="in_progress", session_id=ev.get("session_id", ""),
                channel=ev.get("channel", ""), boot_id=ev.get("boot_id", ""),
                retryable=ev.get("retryable", False),
                start_count=start_count)
        elif kind in ("finish", "interrupted") and tid in records:
            records[tid].status = ("interrupted" if kind == "interrupted"
                                   else ev.get("status", "done"))
            if kind == "finish":
                records[tid].finished = ev.get("finished", 0)

    def plan_recovery(self, interrupted: list = None) -> RecoveryPlan:
        """【P2-4】给中断任务分类,返回恢复计划(不改 scan_interrupted 的返回,兼容)。

        分类规则:
          · retryable=True → retryable(调用方标记过可安全重试)
          · channel 非空   → notify_user(任务来自某个渠道,应通知该渠道用户)
          · 其他           → needs_manual(保守:人工处理)
        每个分类决定都发 recovery.plan 审计事件(有 spine 时)。
        """
        if interrupted is None:
            interrupted = self.scan_interrupted()
        plan = RecoveryPlan()
        for rec in interrupted:
            if rec.retryable:
                plan.retryable.append(rec.task_id)
                action = "retry"
            elif rec.channel:
                plan.notify_user.append(rec.task_id)
                action = "notify_user"
            else:
                plan.needs_manual.append(rec.task_id)
                action = "manual"
            plan.details.append({
                "task_id": rec.task_id,
                "description": rec.description,
                "channel": rec.channel,
                "session_id": rec.session_id,
                "start_count": rec.start_count,
                "reason": "crash_or_restart",
                "action": action,
            })
            self._emit_recovery_event(rec, action)
        return plan

    def _emit_recovery_event(self, rec: TaskRecord, action: str):
        if self._spine is None:
            return
        try:
            self._spine.publish("recovery.plan", {
                "task_id": rec.task_id,
                "action": action,
                "channel": rec.channel,
                "boot_id": self.boot_id,
            }, source="recovery")
        except Exception:
            pass

    def _append(self, entry: dict):
        """追加一条日志(崩溃安全:单行 append)。fsync 可配:默认开(崩溃安全),
        高频场景可关(牺牲最后一条的持久性换吞吐)。"""
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                if self._fsync:
                    os.fsync(f.fileno())     # 落盘,防崩溃丢最后一条
        except Exception:
            pass        # 日志失败不影响主流程

    def compact_log(self, keep_recent_days: float = 7):
        """压实日志:丢弃已完成且超过 N 天的记录,防文件无限膨胀。
        【P2-4】保留判据用 max(started, finished):旧逻辑只看 started,
        会丢"开始得早但刚完成"的记录(其 finish 审计线索一并丢失)。
        步骤级 checkpoint(step 事件)属于被保留的任务 → 一并搬运,否则压实即丢断点。"""
        records = self._replay()
        cutoff = time.time() - keep_recent_days * 86400
        keep = []
        for rec in records.values():
            # 保留:仍在进行的,或最近有活动(开始或完成)的
            last_activity = max(rec.started, rec.finished or 0)
            if rec.status == "in_progress" or last_activity >= cutoff:
                keep.append({"event": "start", **rec.to_dict()})
                if rec.status != "in_progress":
                    keep.append({"event": "finish", "task_id": rec.task_id,
                                 "status": rec.status, "finished": rec.finished})
        keep_ids = {e["task_id"] for e in keep if e.get("event") == "start"}
        steps = self._raw_steps(keep_ids)
        try:
            tmp = self.log_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for e in keep:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
                for e in steps:              # 保留任务的 step 事件随记录一起搬走
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            os.replace(tmp, self.log_path)
        except Exception:
            pass

    def _raw_steps(self, task_ids: set) -> list:
        """扫原始日志取指定任务的 step 事件(compact 用;task_ids 为空直接返回)。"""
        out: list = []
        if not task_ids or not os.path.exists(self.log_path):
            return out
        try:
            with open(self.log_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if ev.get("event") == "step" and ev.get("task_id") in task_ids:
                        out.append(ev)
        except Exception:
            return out
        return out

    def active_count(self) -> int:
        return len(self._active)
