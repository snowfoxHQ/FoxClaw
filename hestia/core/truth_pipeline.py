"""
真值管线 (TruthPipeline)

借鉴 Codex 版 HestiaOS 的 TruthPipeline:用事件总线把"执行结果"自动流转为
"审计记录 → 记忆更新"的真值链。但接到本项目真实的 ThoughtChain 与 HMR,
而非 Codex 版的简化组件。

事件链:
  execution.completed   (某 Agent 执行完任务)
      → trace.recorded   (写入 ThoughtChain 审计)
      → truth.appended   (真值落库)
      → memory.updated   (沉淀到 HMR 共享记忆)

好处:执行方只需 publish 一个 execution.completed 事件,后续审计/记忆流转自动发生,
执行逻辑与审计/记忆解耦(对齐 AHS 的可观测原则)。
"""
from __future__ import annotations
from datetime import datetime


class TruthPipeline:
    """事件驱动的真值管线。订阅执行事件,自动流转到审计与记忆。"""

    def __init__(self, bus, hmr=None):
        self.bus = bus
        self.hmr = hmr                 # 真实 HMR 记忆后端(可空,空则跳过记忆沉淀)
        self.last_trace = None
        self.last_memory_update = None

        # 订阅事件链
        bus.subscribe("execution.completed", self._on_execution_completed)
        bus.subscribe("trace.recorded", self._on_trace_recorded)
        bus.subscribe("truth.appended", self._on_truth_appended)

    def _on_execution_completed(self, msg):
        """执行完成 → 生成审计 trace,发 trace.recorded。"""
        p = msg.payload
        trace = {
            "agent_id": p.get("agent_id"),
            "task": p.get("task"),
            "result_summary": str(p.get("result", ""))[:200],
            "blocked": p.get("blocked", False),
            "schedule": p.get("schedule"),
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.last_trace = trace
        self.bus.publish("trace.recorded", {**p, "trace": trace})

    def _on_trace_recorded(self, msg):
        """审计已记录 → 真值落库,发 truth.appended。"""
        p = msg.payload
        # 真值 = "任务被如此处理"的事实 + 产出要点(信息量足够供日后召回)
        result_brief = str(p.get("result", ""))[:200]
        truth = {
            "fact": (f"任务「{p.get('task')}」由 {p.get('agent_id')} 处理。"
                     f"要点:{result_brief}"),
            "trace": p.get("trace"),
        }
        self.bus.publish("truth.appended", {**p, "truth": truth})

    def _on_truth_appended(self, msg):
        """真值落库 → 沉淀到 HMR 共享记忆,发 memory.updated。"""
        p = msg.payload
        update = {"persisted": False}
        if self.hmr is not None and not p.get("blocked"):
            try:
                # 沉淀到 HMR 共享层(所有 Agent 可见),用真实的 ingest 接口
                content = p.get("truth", {}).get("fact", "")
                self.hmr.ingest(
                    content,
                    type="execution",
                    agent_id=p.get("agent_id", "system"),
                    title=f"真值_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    tags=["truth_pipeline"],
                    shared=True,
                )
                update = {"persisted": True, "content": content}
            except Exception as e:
                update = {"persisted": False, "error": str(e)}
        self.last_memory_update = update
        self.bus.publish("memory.updated", {**p, "memory_update": update})

    def emit_execution(self, agent_id: str, task: str, result, blocked: bool = False,
                       schedule: dict = None):
        """便捷入口:执行方调用它来启动整条真值链。"""
        return self.bus.publish("execution.completed", {
            "agent_id": agent_id, "task": task, "result": result,
            "blocked": blocked, "schedule": schedule,
        })
