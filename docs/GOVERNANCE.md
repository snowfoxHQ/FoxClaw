# Foxclaw 运行时治理能力说明

这组能力借鉴 OpenClaw 等成熟 Agent 系统的设计原理,用 Python 适配 Foxclaw 实现,目标是让系统在真实运行(消息重发、进程崩溃、长任务、死循环)中保持**可靠、可审计、不丢不重**。

六项能力分两类:**已接入主路径、现在就生效**的(幂等、重启恢复、循环检测),和**基础设施、按需启用**的(上下文压缩、钩子标准化、干预队列)。

---

## 一、幂等守卫(Idempotency)— 已生效

**解决:** 消息平台天生会重发(网络抖动、平台重试、轮询游标边界)。没有幂等,用户会收到重复回复、同一操作被记两次。

**原理:** 给"只应发生一次"的操作算一个稳定 key,执行前**原子查重**——处理过就跳过,没处理才执行并记录。

**核心接口**(`foxclaw/core/idempotency.py`):
```python
from foxclaw.core.idempotency import IdempotencyGuard, make_key

guard = IdempotencyGuard(ttl_seconds=3600)        # 可选 persist_path 跨重启
key = make_key("message", channel, message_id, sender_id)
if guard.check_and_mark(key):     # 原子:首次返回 True(执行),重复返回 False(跳过)
    do_the_work()
```

**已接入:** `ChannelDispatcher.handle` 自动按 `channel+message_id+sender` 去重;无 message_id 的消息退化为"渠道+发送者+内容+10秒时间窗"兜底。

**设计要点:**
- `check_and_mark` 在同一把锁里完成查重+标记(防并发重发都去执行,50线程压测验证恰好1个成功)
- TTL 过期清理 + 容量上限 LRU 淘汰(防无限膨胀)
- 可选落盘(原子替换写),跨进程重启仍识别"处理过了"

---

## 二、重启恢复(Restart Recovery)— 已生效

**解决:** 长任务(多 Agent 链、财务分析)跑到一半进程崩了/重启,任务凭空消失,记忆可能留下半完成的脏数据。

**原理:** 启动时主动识别"上次还在进行、但持有它的进程已不在"的任务,**诚实标记为中断**,而不是装无事。契合 Foxclaw"可审计、可信"的定位。

**核心接口**(`foxclaw/core/recovery.py`):
```python
from foxclaw.core.recovery import RestartRecovery

rec = RestartRecovery(log_path="data/task_log.jsonl")
rec.task_started(task_id, description)    # 任务开始(落 in_progress)
rec.task_finished(task_id, status="done")  # 结束(done/failed)
interrupted = rec.scan_interrupted()       # 启动时扫描:返回被中断的任务列表
```

**已接入:** `Foxclaw` 启动时自动扫描,检测到中断任务会打印提示;主管任务经 base 模板的
生命周期钩子(`_on_session_started`/`_on_task_settled`,P1-1)自动记录开始/结束
(正常终态标 done、未兜底异常标 failed;`run_task` 与 `run_task_stream` 两条路径都记账)。

**设计要点:**
- **boot_id 防 pid 复用**:每次启动生成唯一 ID,判据是"非本次启动的 in_progress 任务=被中断"。比单纯靠 pid 可靠——pid 会被 OS 复用,旧任务的 pid 被新进程占用会导致漏判。
- JSONL 追加写 + fsync(崩溃安全:单行 append 原子,损坏行自动跳过)
- `compact_log` 防日志无限膨胀

---

## 三、工具循环检测(Loop Detection)— 已生效

**解决:** 断路器管"失败",但管不了"无进展死循环"——Agent 不报错却陷入重复/横跳/轮询卡死,白烧 token。

**原理:** 监控工具调用历史,检测三种循环模式,多级阈值预警→熔断。

**核心接口**(`foxclaw/harness/loop_detection.py`):
```python
from foxclaw.harness.loop_detection import ToolLoopDetector

det = ToolLoopDetector(warning=6, critical=12, circuit_break=20)
verdict = det.record(tool_name, args, result)   # 每次工具调用后
if verdict.stuck:
    print(verdict.detector, verdict.message)     # repeat/ping_pong/no_progress
if det.should_abort():
    abort()                                       # 达熔断阈值
```

**已接入:** `SkillAdapter` 每次工具调用后自动检测,卡死时在结果上附 `loop_warning` 并触发 `on_loop` 钩子。

**三种模式:** `repeat`(同调用连续重复)、`ping_pong`(两调用交替)、`no_progress`(轮询结果无变化,优先级最高,语义最精确)。

---

## 四、上下文压缩(Compaction)— 基础设施

**解决:** 长对话撑爆上下文窗口。

**原理:** 渐进降级压缩,绝不崩——LLM 完整摘要 → 排除超大消息的部分摘要 → 截断兜底。始终保留系统提示 + 最近 N 轮 + 关键标识符(ID/金额)。

**核心接口**(`foxclaw/core/compaction.py`):
```python
from foxclaw.core.compaction import Compactor

c = Compactor(router=model_router, max_tokens=6000, keep_recent=4)
if c.needs_compaction(messages):
    result = c.compact(messages)     # result.messages 是压缩后的列表
```

**设计要点:** 压缩后校验是否真变小(省不到 10% 就放弃,method=`skipped_ineffective`),避免系统消息过大时压缩反而变大。`router=None` 时直接走截断兜底。

---

## 五、生命周期钩子标准化(Hooks)— 基础设施

**解决:** 钩子点命名零散、阻断权不清晰。

**原理:** 标准生命周期钩子 + 明确的阻断权 + 向后兼容。

**标准钩子点**(`foxclaw/skills/hooks_spec.py`):
- 会话级:`session_start` → `agent_start` → `agent_end` → `session_end`
  - `agent_start`(别名 **`pre_reasoning`**)/ `agent_end`(别名 **`task_complete`**)由**认知管线 `run_task`** 触发(E2);`agent_start` 可返回 `additional_context` 注入本次推理(非阻断)。`session_start/end` 由 AHS skill 编排链触发。
- 工具级:`before_tool_call`(唯一可阻断)→ `after_tool_call` / `on_error` / `on_loop`
- 消息级:`before_message_write`

```python
from foxclaw.skills.lifecycle import HookManager
hm = HookManager()
hm.register("before_tool_call", my_hook)    # 旧名 pre_skill 也兼容,自动规整
```

**设计要点:** 只有 `before_tool_call` 有阻断权(返回 `{block:True}` 生效);旧名(pre_skill/post_skill)自动规整为标准名且互通;非法钩子名会记警告(防拼错静默失效)。

---

## 六、干预队列(Steering Queue)— 基础设施(为并行预留)

**解决:** 并行子 Agent 的结果如何安全回收——不丢、不重复。

**原理:** 租约机制。当前多 Agent 顺序执行用不上(直接收集即可);这是为**未来并行子 Agent** 预留的基础设施。

**核心接口**(`foxclaw/core/steering_queue.py`):
```python
from foxclaw.core.steering_queue import SteeringQueue

q = SteeringQueue(lease_seconds=30)
q.publish(requester, source_agent, payload)   # 子任务完成入队
items = q.lease(requester)                      # 消费者租走(不会被重复租)
q.ack(item.item_id, item.lease_id)             # 确认处理完移除
# 过期未 ack 的项自动回到 pending(主任务崩溃兜底)
# 顺序场景便捷:q.drain(requester) 一次性取走(尊重租约,跳过被租用项)
```

**设计要点:** 锁内挑选+标记(并发不重复租用);租约过期自动重入队(结果不丢);ack 校验 lease_id(防过期租约误删别人重新租走的项)。

---

## 测试

全部纳入回归(`python tests/run_all.py`):
- `test_governance.py` — 六项治理能力(幂等并发、boot_id 防复用、租约不重复等)
- `test_channels.py` — 渠道适配器 + 闭环 + 幂等去重

---

## 设计原则小结

这组能力反复体现几个原则,可供后续开发参考:

- **操作要校验是否真达到目的**:压缩后检查真变小了吗、幂等并发下真只执行一次吗——不假设操作一定有效。
- **静默失效最危险**:拼错钩子名、解密缺库、消息处理失败被吞——都要让问题可见(警告/last_error),而非默默不工作。
- **崩溃后不装没事**:重启恢复主动交代中断的任务,契合"可信"定位。
- **别只信 pid**:进程恢复要用 boot_id 这类不复用的标识,pid 会被 OS 复用。
- **便捷接口别破坏核心保证**:drain 要尊重租约,否则破坏"不重复"。
