"""
共享层写入审计落盘 sink —— P0-3 配套

为什么必须落盘
──────────────
EventSpine._history 是 deque(maxlen=1000) 的**进程内环形缓冲**,且全系统事件共享:
高频事件(agent.cognition.* / knowledge.created)会很快把 memory.shared.write 冲掉,
进程退出即全丢。而 P0-3 切 enforce 的判据恰恰是「真实流量中有没有**合法的**写入被拒」
—— 没有落盘,这个样本永远积累不起来(总不能让人蹲守 /api/bus/history),策略表就
只能停在「自洽的假设」上,灰度推进(audit→warn→enforce)没有事实依据。

行为
────
订阅 EventSpine 的 memory.shared.write 主题,逐条 JSON 追加到:
  {data_dir}/audit/shared_write.jsonl
每行:{"ts": ISO 时间, "topic": ..., "correlation_id": ..., "payload": {原始审计字段}}
payload 即 guard_shared_write 发布的全部字段(agent_id/type/tags/allowed/reason/mode/
content_sha/content_preview)——正文只留 60 字符预览 + 16 位哈希,不落敏感全文。

写入失败一律静默吞掉 —— **审计绝不影响写入主流程**(与 guard_shared_write 同一设计约束)。

开关:FOXCLAW_SHARED_WRITE_AUDIT=0 关闭(默认开;每条 <300 字节,量级可忽略)。
判读:用 read() 或直接看文件 —— allowed=false 的每一行都要逐条判读
(是真越界,还是策略表误伤),全部为真越界且无一条误伤,方可切 enforce。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from . import audit_chain

ENV_SWITCH = "FOXCLAW_SHARED_WRITE_AUDIT"
AUDIT_DIR = "audit"
AUDIT_FILE = "shared_write.jsonl"


class SharedWriteAuditSink:
    """把 memory.shared.write 审计事件追加落盘。

    线程安全:EventSpine 的 handler 在发布线程、锁外被调用(脊柱只锁快照),
    并发写入同一文件必须串行 —— 用进程内锁。多进程部署(FOXCLAW_ALLOW_INSECURE
    反对但允许)各自 append 行,JSONL 每行自包含,不会互相破坏。
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._last_hash: str | None = None    # 链尾,首次写时从文件尾部补齐
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, evt):  # EventSpine.subscribe 的 handler 形态:handler(event)
        try:
            rec = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "topic": evt.topic,
                "correlation_id": getattr(evt, "correlation_id", ""),
                "payload": evt.payload,
            }
            with self._lock:
                if self._last_hash is None:
                    self._last_hash = audit_chain.tail_hash(self.path)
                rec = audit_chain.link(rec, self._last_hash)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                self._last_hash = rec["hash"]
        except Exception:
            pass  # 审计失败绝不影响写入主流程

    def read(self, limit: int = 200) -> list:
        """读最近 limit 条(供诊断/工具用)。坏行跳过 —— 落盘绝不该反过来卡死读取。"""
        try:
            with open(self.path, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return []
        out = []
        for ln in lines[-limit:]:
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out

    def verify(self) -> tuple:
        """校验落盘链 → (ok, 条数, 问题)。"""
        return audit_chain.verify(self.path)


def attach(spine, data_dir):
    """把 sink 挂到脊柱,返回 sink 实例;FOXCLAW_SHARED_WRITE_AUDIT=0 时不挂(返回 None)。"""
    if str(os.environ.get(ENV_SWITCH, "")).strip() == "0":
        return None
    sink = SharedWriteAuditSink(Path(data_dir) / AUDIT_DIR / AUDIT_FILE)
    spine.subscribe("memory.shared.write", sink)
    return sink
