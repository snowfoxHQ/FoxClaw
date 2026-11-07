"""
Foxclaw Core — Token 计量器 (Token Meter)

成本可观测性,运行时治理的一块。埋在 ModelRouter.complete() 这个唯一的 LLM
调用咽喉处,所有模型调用自动被记录,不漏一次。

为什么放这里:整个系统真正调 LLM 的入口只有 router.complete()——认知层
(记忆/Harness/ThoughtChain/领域内核)全是本地算法不烧 token。所以只要拦住这一处,
就能完整统计 token 消耗。

token 数来源优先级(从准到糙):
  1. API 返回的真实 usage(anthropic/openai 都带)—— 最准
  2. tiktoken 离线估算(装了 tiktoken)
  3. 字符数估算(兜底,中文~1.5字符/token、英文~4字符/token,混合按 ~2.2)

记录维度:provider/model、agent_id、输入/输出 token、是否估算、时间。
可按 agent / provider 聚合,可落盘(JSONL 追加,崩溃安全)。
"""
from __future__ import annotations
import json
import os
import threading
import time
from dataclasses import dataclass, asdict


def estimate_tokens(text: str, model: str = "") -> int:
    """估算文本 token 数。优先 tiktoken,降级字符估算。"""
    if not text:
        return 0
    try:
        import tiktoken
        try:
            enc = tiktoken.encoding_for_model(model)
        except Exception:
            enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        # 字符估算:中文(非ASCII)按 1.1 字符/token,英文(ASCII)按 4 字符/token。
        ascii_chars = sum(1 for c in text if ord(c) < 128)
        cjk_chars = len(text) - ascii_chars
        return int(ascii_chars / 4 + cjk_chars * 1.1) + 1


@dataclass
class CallRecord:
    timestamp: float
    provider: str
    model: str
    agent_id: str
    input_tokens: int
    output_tokens: int
    estimated: bool          # True=估算,False=API真实usage
    task_kind: str = ""      # 可选:single/multi/...

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total_tokens"] = self.total_tokens
        return d


class TokenMeter:
    """Token 计量器。线程安全,可聚合,可落盘。"""

    def __init__(self, log_path: str = None, max_records: int = 10000,
                 max_log_mb: float = 10.0):
        self.log_path = log_path
        self.max_records = max_records
        self.max_log_bytes = int(max_log_mb * 1024 * 1024)
        self._records: list[CallRecord] = []
        self._lock = threading.RLock()
        self._append_count = 0     # 触发轮转检查的计数器
        if log_path:
            os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)

    def record(self, provider: str, model: str, agent_id: str,
               input_tokens: int, output_tokens: int, estimated: bool,
               task_kind: str = "") -> CallRecord:
        rec = CallRecord(
            timestamp=time.time(), provider=provider, model=model,
            agent_id=agent_id or "unknown",
            input_tokens=int(input_tokens), output_tokens=int(output_tokens),
            estimated=estimated, task_kind=task_kind)
        with self._lock:
            self._records.append(rec)
            if len(self._records) > self.max_records:
                self._records = self._records[-self.max_records:]
            self._append(rec)
        return rec

    def measure_call(self, *, system: str, messages: list, output: str,
                     provider: str, model: str, agent_id: str = "",
                     usage: dict = None, task_kind: str = "") -> CallRecord:
        """计量一次调用。优先用 API 返回的 usage,否则估算。

        usage 形如 {"input_tokens": N, "output_tokens": M}(已由 router 归一化)。
        """
        if usage and usage.get("input_tokens") is not None:
            return self.record(provider, model, agent_id,
                               usage.get("input_tokens", 0),
                               usage.get("output_tokens", 0),
                               estimated=False, task_kind=task_kind)
        # 估算:输入 = system + 所有 message content,输出 = output
        input_text = (system or "") + "\n".join(
            str(m.get("content", "")) for m in (messages or []))
        in_tok = estimate_tokens(input_text, model)
        out_tok = estimate_tokens(output or "", model)
        return self.record(provider, model, agent_id, in_tok, out_tok,
                           estimated=True, task_kind=task_kind)

    # ── 聚合查询 ──────────────────────────────────────────
    def summary(self) -> dict:
        """总体统计。"""
        with self._lock:
            recs = list(self._records)
        if not recs:
            return {"calls": 0, "total_tokens": 0, "input_tokens": 0,
                    "output_tokens": 0, "estimated_ratio": 0.0}
        total_in = sum(r.input_tokens for r in recs)
        total_out = sum(r.output_tokens for r in recs)
        est = sum(1 for r in recs if r.estimated)
        return {
            "calls": len(recs),
            "total_tokens": total_in + total_out,
            "input_tokens": total_in,
            "output_tokens": total_out,
            "avg_tokens_per_call": round((total_in + total_out) / len(recs), 1),
            "estimated_ratio": round(est / len(recs), 2),
        }

    def by_agent(self) -> dict:
        """按 Agent 聚合 token 消耗(找出最费 token 的 Agent)。"""
        with self._lock:
            recs = list(self._records)
        agg: dict[str, dict] = {}
        for r in recs:
            a = agg.setdefault(r.agent_id, {"calls": 0, "total_tokens": 0,
                                            "input_tokens": 0, "output_tokens": 0})
            a["calls"] += 1
            a["input_tokens"] += r.input_tokens
            a["output_tokens"] += r.output_tokens
            a["total_tokens"] += r.total_tokens
        return dict(sorted(agg.items(), key=lambda kv: -kv[1]["total_tokens"]))

    def by_provider(self) -> dict:
        """按 provider/model 聚合(估算不同模型的成本分布)。"""
        with self._lock:
            recs = list(self._records)
        agg: dict[str, dict] = {}
        for r in recs:
            key = f"{r.provider}/{r.model}"
            a = agg.setdefault(key, {"calls": 0, "total_tokens": 0})
            a["calls"] += 1
            a["total_tokens"] += r.total_tokens
        return dict(sorted(agg.items(), key=lambda kv: -kv[1]["total_tokens"]))

    def estimate_cost(self, price_per_1k_input: float = 0.0,
                      price_per_1k_output: float = 0.0) -> dict:
        """按给定单价估算总成本(单位由调用方定,如人民币元/1k token)。

        DeepSeek 等价格便宜,默认单价为 0(调用方传入实际单价才算)。
        """
        s = self.summary()
        cost = (s["input_tokens"] / 1000 * price_per_1k_input +
                s["output_tokens"] / 1000 * price_per_1k_output)
        return {
            "input_tokens": s["input_tokens"],
            "output_tokens": s["output_tokens"],
            "price_per_1k_input": price_per_1k_input,
            "price_per_1k_output": price_per_1k_output,
            "estimated_cost": round(cost, 4),
        }

    def report(self) -> str:
        """人类可读的文本报告。"""
        s = self.summary()
        if s["calls"] == 0:
            return "Token 计量:暂无 LLM 调用记录(可能全程 stub 模式)。"
        lines = [
            "═" * 40,
            "Token 消耗报告",
            "═" * 40,
            f"总调用次数 : {s['calls']}",
            f"总 token   : {s['total_tokens']:,}(输入 {s['input_tokens']:,} / 输出 {s['output_tokens']:,})",
            f"平均每次   : {s['avg_tokens_per_call']:,} token",
            f"估算占比   : {int(s['estimated_ratio']*100)}%(其余为 API 真实 usage)",
            "",
            "按 Agent:",
        ]
        for agent, a in self.by_agent().items():
            lines.append(f"  {agent:12} {a['total_tokens']:>8,} token  ({a['calls']} 次)")
        lines.append("")
        lines.append("按模型:")
        for key, a in self.by_provider().items():
            lines.append(f"  {key:24} {a['total_tokens']:>8,} token  ({a['calls']} 次)")
        lines.append("═" * 40)
        return "\n".join(lines)

    def reset(self):
        with self._lock:
            self._records.clear()

    def _append(self, rec: CallRecord):
        if not self.log_path:
            return
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
        except Exception:
            pass        # 计量落盘失败不影响主流程
        # 每 100 次追加检查一次文件大小,超限则轮转(防磁盘无限膨胀)
        self._append_count += 1
        if self._append_count % 100 == 0:
            self._rotate_if_needed()

    def _rotate_if_needed(self):
        """日志文件超过上限时,保留最近一半,丢弃更早的(防无限增长)。"""
        try:
            if not self.log_path or not os.path.exists(self.log_path):
                return
            if os.path.getsize(self.log_path) < self.max_log_bytes:
                return
            with open(self.log_path, encoding="utf-8") as f:
                lines = f.readlines()
            keep = lines[len(lines) // 2:]      # 保留较新的一半
            tmp = self.log_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(keep)
            os.replace(tmp, self.log_path)      # 原子替换
        except Exception:
            pass        # 轮转失败不影响主流程
