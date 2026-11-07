"""
真值管线 (TruthPipeline)

借鉴 Codex 版 Foxclaw 的 TruthPipeline:用事件总线把"执行结果"自动流转为
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
    """事件驱动的真值管线。订阅执行事件,自动流转到审计与记忆。

    入队方式:任何持有**同一个 EventBus** 的组件,只要 publish execution.completed 即可
    启动整条真值链,例如:

        bus.publish("execution.completed",
                    {"agent_id": ..., "task": ..., "result": ..., "blocked": ...})

    项目内部的实际入口是 agents/base.py 的 CognitiveAgent._emit_execution()
    (经 EventSpine 转发到同一事件名)。本类**不再自带便捷转发方法** —— 事件名本身就是
    契约,多留一个转发壳只会让「谁能触发真值链」存在两种说法(P3-9 死代码清理)。
    """

    def __init__(self, bus, hmr=None):
        self.bus = bus
        self.hmr = hmr                 # 真实 HMR 记忆后端(可空,空则跳过记忆沉淀)
        self.last_trace = None
        self.last_memory_update = None

        # 订阅事件链。【P4-1】三个都是精确 channel 名 → exact=True:
        # 门面化后 SystemBus 默认是前缀匹配,不锁定的话 "execution.completed.extra"
        # 之类的前缀事件也会触发真值链(语义漂移)。
        bus.subscribe("execution.completed", self._on_execution_completed, exact=True)
        bus.subscribe("trace.recorded", self._on_trace_recorded, exact=True)
        bus.subscribe("truth.appended", self._on_truth_appended, exact=True)

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
        """审计已记录 → 真值落库,发 truth.appended。

        【P3-5】blocked=True 的任务不沉淀真值:被拦截 ≠ 真值。只留审计 trace
        (trace.recorded 照常发——审计要记录"发生过什么"),发 truth.skipped 事件
        供前端/审计观测"这次真值沉淀被跳过",不进入真值链。否则订阅 truth.appended
        的下游(如 TruthCrystallizer)会把被拦截的任务当真值结晶进 HKC。
        """
        p = msg.payload
        if p.get("blocked"):
            self.last_memory_update = {"persisted": False, "reason": "blocked"}
            self.bus.publish("truth.skipped", {**p, "reason": "blocked"})
            return
        # 真值 = "任务被如此处理"的事实 + 产出要点(信息量足够供日后召回)
        result_brief = str(p.get("result", ""))[:200]
        truth = {
            "fact": (f"任务「{p.get('task')}」由 {p.get('agent_id')} 处理。"
                     f"要点:{result_brief}"),
            "trace": p.get("trace"),
        }
        self.bus.publish("truth.appended", {**p, "truth": truth})

    def _on_truth_appended(self, msg):
        """真值落库 → 沉淀到 HMR 共享记忆,发 memory.updated。

        【P3-0】写共享层用 agent_id="system":真值沉淀是系统级管线行为,不是某个
        Agent 的自主写入——若按来源 Agent 归因,memory.shared.write 审计事件会把
        系统写入误记成该 Agent 的写入(审计归因失真)。来源 Agent 保留在 tags 的
        origin:<id> 里,便于追溯。权限侧由 SharedWritePolicy 的 facet 全局豁免
        (tags 含 "truth_pipeline")+ 策略表 system:{"*"} 双通道保证不被误拦。

        【P3-5】双保险:_on_trace_recorded 已挡 blocked,这里再检查一次。
        """
        p = msg.payload
        update = {"persisted": False}
        if self.hmr is not None and not p.get("blocked"):
            try:
                # 沉淀到 HMR 共享层(所有 Agent 可见),用真实的 ingest 接口
                content = p.get("truth", {}).get("fact", "")
                origin_agent = p.get("agent_id", "unknown")
                self.hmr.ingest(
                    content,
                    type="execution",
                    agent_id="system",      # 【P3-0】系统级写入(非来源 Agent)
                    title=f"真值_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    tags=["truth_pipeline", f"origin:{origin_agent}"],   # 来源可追溯
                    shared=True,
                )
                update = {"persisted": True, "content": content}
            except Exception as e:
                update = {"persisted": False, "error": str(e)}
        self.last_memory_update = update
        self.bus.publish("memory.updated", {**p, "memory_update": update})
