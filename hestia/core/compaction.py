"""
Hestia Core — 上下文压缩 (Compaction)

⚠ 【预留组件·无生产接入点】(2026-07-17 V3·B1 审核标注)
   本压缩器**无任何生产调用路径**(仅 tests/test_governance.py::test_compaction 覆盖——
   说明它曾被认真实现+验证,只是没接进执行)。根因:HestiaOS 领域 agent 每次
   run_task/stream_reply 的 messages 都是**单轮新建**、不累积长对话历史;channel 层的
   conversation_id 是回复路由 + 记忆隔离 key(见 dispatcher),**不维护 LLM 对话历史缓冲**。
   故当前**没有会撑爆上下文窗口的长历史可压**。
   这是**能力预留**,不是在工作的机制。若未来出现长会话累积路径(如真做多轮对话记忆),
   在其 LLM 调用前接入 `Compactor.needs_compaction()` → `compact()` 即可。
   —— 见 docs/internal/AGENT_HARNESS_REMEDIATION_PLAN.md「未接线清单」。

借鉴 OpenClaw 的渐进降级压缩思路,适配 Hestia。长对话会撑爆上下文窗口,
压缩把早期历史摘要化,腾出空间,同时保住关键信息(ID/数字等标识符不丢)。

策略(渐进降级,任一级失败退到下一级,绝不崩):
  1. LLM 摘要:把早期消息交给模型生成摘要(保留标识符)
  2. 部分摘要:若含超大消息,排除它们再摘要
  3. 截断兜底:摘要都失败 → 保留首尾、中间用占位说明替代
始终保留:系统提示 + 最近 keep_recent 轮原样(近期上下文最重要)。
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field


# 粗略 token 估算:中文约 1 字符 ≈ 1 token,英文约 4 字符 ≈ 1 token。
def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(text) - cjk
    return cjk + other // 4 + 1


def estimate_messages_tokens(messages: list) -> int:
    return sum(estimate_tokens(str(m.get("content", ""))) for m in messages)


# 关键标识符:不能在摘要中丢失的(ID、长数字、订单号、哈希、金额等)
# 不用外层 \b:¥/逗号不是单词字符,会导致金额匹配失败。
_IDENTIFIER_PAT = re.compile(
    r"[a-zA-Z]{2,}_[a-zA-Z0-9]{4,}|[a-f0-9]{8,}|[0-9]{6,}|¥[\d,]+(?:\.\d+)?")


def extract_identifiers(messages: list) -> list[str]:
    """从消息里提取关键标识符,确保压缩后仍可引用。"""
    ids = []
    for m in messages:
        for match in _IDENTIFIER_PAT.findall(str(m.get("content", ""))):
            if match not in ids:
                ids.append(match)
    return ids[:50]


@dataclass
class CompactionResult:
    messages: list = field(default_factory=list)     # 压缩后的消息列表
    method: str = "none"                             # llm_summary/partial_summary/truncate/none
    original_count: int = 0
    compacted_count: int = 0
    original_tokens: int = 0
    compacted_tokens: int = 0
    preserved_identifiers: list = field(default_factory=list)
    note: str = ""                                   # 说明(如压缩跳过原因)

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "original_count": self.original_count,
            "compacted_count": self.compacted_count,
            "original_tokens": self.original_tokens,
            "compacted_tokens": self.compacted_tokens,
            "saved_tokens": self.original_tokens - self.compacted_tokens,
            "preserved_identifiers": self.preserved_identifiers,
            "note": self.note,
        }


class Compactor:
    """上下文压缩器。超阈值时把早期历史摘要化,保留系统提示与近期消息。"""

    def __init__(self, router=None, max_tokens: int = 6000,
                 keep_recent: int = 4, oversized_token: int = 2000):
        self.router = router                 # ModelRouter,用于 LLM 摘要(可空→走截断兜底)
        self.max_tokens = max_tokens         # 超过此 token 触发压缩
        self.keep_recent = keep_recent       # 最近保留几条原样
        self.oversized_token = oversized_token  # 单条超此值视为"超大消息"

    def needs_compaction(self, messages: list) -> bool:
        return estimate_messages_tokens(messages) > self.max_tokens

    def compact(self, messages: list) -> CompactionResult:
        """压缩消息列表。渐进降级,绝不抛异常。"""
        res = CompactionResult(
            original_count=len(messages),
            original_tokens=estimate_messages_tokens(messages))

        if not self.needs_compaction(messages):
            res.messages = list(messages)
            res.method = "none"
            res.compacted_count = len(messages)
            res.compacted_tokens = res.original_tokens
            return res

        # 分区:系统消息 / 待压缩的早期消息 / 保留的近期消息
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        keep = max(0, self.keep_recent)
        recent = non_system[-keep:] if keep else []
        to_compress = non_system[:-keep] if keep else non_system

        if not to_compress:
            res.messages = list(messages)
            res.method = "none"
            res.compacted_count = len(messages)
            res.compacted_tokens = res.original_tokens
            return res

        res.preserved_identifiers = extract_identifiers(to_compress)

        # —— 渐进降级 ——
        summary = None
        method = "truncate"
        # 1. LLM 完整摘要
        if self.router is not None:
            summary = self._try_summarize(to_compress, res.preserved_identifiers)
            if summary:
                method = "llm_summary"
        # 2. 部分摘要:排除超大消息后再试
        if summary is None and self.router is not None:
            trimmed = [m for m in to_compress
                       if estimate_tokens(str(m.get("content", ""))) <= self.oversized_token]
            if trimmed and len(trimmed) < len(to_compress):
                summary = self._try_summarize(trimmed, res.preserved_identifiers)
                if summary:
                    method = "partial_summary"
        # 3. 截断兜底
        if summary is None:
            summary = self._truncate_fallback(to_compress, res.preserved_identifiers)
            method = "truncate"

        summary_msg = {"role": "system",
                       "content": f"【早期对话摘要(已压缩 {len(to_compress)} 条)】\n{summary}"}
        compacted = system_msgs + [summary_msg] + recent
        compacted_tokens = estimate_messages_tokens(compacted)

        # 校验:压缩须真的显著变小(至少省 10%),否则放弃——避免系统消息过大时
        # 压缩无效还白加摘要导致反而更大。
        if compacted_tokens >= res.original_tokens * 0.9:
            res.messages = list(messages)
            res.method = "skipped_ineffective"
            res.compacted_count = len(messages)
            res.compacted_tokens = res.original_tokens
            # 提示:压缩无效通常因系统消息/近期消息本身已占满预算
            sys_tokens = estimate_messages_tokens(system_msgs)
            res.note = (f"压缩跳过:有效压缩空间不足(系统消息占 {sys_tokens} token)。"
                        f"建议精简系统提示或调大上下文预算。")
            return res

        res.messages = compacted
        res.method = method
        res.compacted_count = len(res.messages)
        res.compacted_tokens = compacted_tokens
        return res

    def _try_summarize(self, messages: list, identifiers: list) -> str | None:
        """用 LLM 摘要,失败返回 None(交给下一级降级)。"""
        try:
            convo = "\n".join(
                f"{m.get('role','?')}: {str(m.get('content',''))[:1000]}"
                for m in messages)
            id_hint = ("\n必须原样保留这些标识符: " + ", ".join(identifiers)
                       if identifiers else "")
            sys_prompt = ("你是对话摘要助手。把以下对话压缩成简洁摘要,保留关键决策、"
                          "事实、未完成事项。" + id_hint)
            out = self.router.complete(
                system=sys_prompt,
                messages=[{"role": "user", "content": f"对话内容:\n{convo}\n\n请输出摘要:"}],
                max_tokens=800, agent_id="_compaction")
            if out and isinstance(out, str) and len(out.strip()) > 10:
                return out.strip()
        except Exception:
            pass
        return None

    def _truncate_fallback(self, messages: list, identifiers: list) -> str:
        """摘要失败的兜底:保留首尾消息要点 + 关键标识符,中间略去。"""
        parts = []
        if messages:
            first = str(messages[0].get("content", ""))[:150]
            parts.append(f"对话开始: {first}")
        if len(messages) > 2:
            parts.append(f"(中间 {len(messages)-2} 条消息略)")
        if len(messages) > 1:
            last = str(messages[-1].get("content", ""))[:150]
            parts.append(f"最近: {last}")
        if identifiers:
            parts.append("关键标识符: " + ", ".join(identifiers))
        return "\n".join(parts)
