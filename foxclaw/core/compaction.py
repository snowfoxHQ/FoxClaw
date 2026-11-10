"""
Foxclaw Core — 上下文压缩 (Compaction)

⚠ 【预留组件·无生产接入点】(2026-07-17 V3·B1 审核标注)
   本压缩器**无任何生产调用路径**(仅 tests/test_governance.py::test_compaction 覆盖——
   说明它曾被认真实现+验证,只是没接进执行)。根因:Foxclaw 领域 agent 每次
   run_task/stream_reply 的 messages 都是**单轮新建**、不累积长对话历史;channel 层的
   conversation_id 是回复路由 + 记忆隔离 key(见 dispatcher),**不维护 LLM 对话历史缓冲**。
   故当前**没有会撑爆上下文窗口的长历史可压**。
   这是**能力预留**,不是在工作的机制。若未来出现长会话累积路径(如真做多轮对话记忆),
   在其 LLM 调用前接入 `Compactor.needs_compaction()` → `compact()` 即可。
   —— 见 docs/internal/AGENT_HARNESS_REMEDIATION_PLAN.md「未接线清单」。

借鉴 OpenClaw 的渐进降级压缩思路,适配 Foxclaw。长对话会撑爆上下文窗口,
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


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(text) - cjk
    return cjk + other // 4 + 1


def estimate_messages_tokens(messages: list) -> int:
    return sum(estimate_tokens(str(m.get("content", ""))) for m in messages)


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
    messages: list = field(default_factory=list)
    method: str = "none"
    original_count: int = 0
    compacted_count: int = 0
    original_tokens: int = 0
    compacted_tokens: int = 0
    preserved_identifiers: list = field(default_factory=list)
    note: str = ""

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


def group_tool_calls(messages: list) -> list:
    """把 messages 切成**不可分割的原子组**列表。

    function-calling 会话中,`assistant`(带 tool_calls)与其后续 `tool` 结果
    必须**成对保留** —— 孤立引用会被 OpenAI/Anthropic 拒绝(400:
    "tool_call_id not found" / "unexpected tool role")。

    病根:原实现只按 `role == "system"` 分区、再按**条数**切 keep_recent,
    边界若切在一轮中间就会切出「有 tool 结果、但其 assistant.tool_calls
    已被摘要掉」的碎片。base._reply_with_tools 每轮追加 1 个 assistant
    (带 N 个 tool_calls)+ N 个 tool 结果,故一轮内多工具时必现。
    实测:一轮 2 个工具 + keep_recent=3 → 孤立 call_2 → 送回 provider 必 400。

    本函数按「声明-结果」结构分组,调用方改为**按组**保留,保证配对完整。
    """
    groups: list[list[dict]] = []
    buf: list[dict] = []
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        buf.append(m)
        if m.get("role") == "assistant" and (m.get("tool_calls") or []):
            ids = {tc.get("id") for tc in (m.get("tool_calls") or [])}
            j = i + 1
            while j < n:
                nxt = messages[j]
                if nxt.get("role") == "tool" and nxt.get("tool_call_id") in ids:
                    buf.append(nxt)
                    j += 1
                else:
                    break
            i = j
        else:
            i += 1
        groups.append(buf)
        buf = []
    return groups


class Compactor:
    """上下文压缩器。超阈值时把早期历史摘要化,保留系统提示与近期消息。"""

    def __init__(self, router=None, max_tokens: int = 6000,
                 keep_recent: int = 4, oversized_token: int = 2000):
        self.router = router
        self.max_tokens = max_tokens
        self.keep_recent = keep_recent
        self.oversized_token = oversized_token

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

        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        keep = max(0, self.keep_recent)
        _groups = group_tool_calls(non_system)
        if keep and _groups:
            recent = [m for g in _groups[-keep:] for m in g]
            to_compress = [m for g in _groups[:-keep] for m in g]
        elif keep:
            recent, to_compress = [], non_system
        else:
            recent, to_compress = [], non_system
        _declared = {tc.get("id") for m in (recent + to_compress)
                     for tc in (m.get("tool_calls") or [])}
        _orphans = [m for m in (recent + to_compress)
                    if m.get("role") == "tool" and m.get("tool_call_id") not in _declared]
        if _orphans:
            res.messages = list(messages)
            res.method = "skipped_unpaired_tool"
            res.compacted_count = len(messages)
            res.compacted_tokens = res.original_tokens
            res.note = (f"压缩跳过:检测到 {len(_orphans)} 条孤立 tool 消息"
                        f"(其 tool_calls 声明不在保留集内),压缩会触发 provider 400。")
            return res

        if not to_compress:
            res.messages = list(messages)
            res.method = "none"
            res.compacted_count = len(messages)
            res.compacted_tokens = res.original_tokens
            return res

        res.preserved_identifiers = extract_identifiers(to_compress)

        summary = None
        method = "truncate"
        if self.router is not None:
            summary = self._try_summarize(to_compress, res.preserved_identifiers)
            if summary:
                method = "llm_summary"
        if summary is None and self.router is not None:
            trimmed = [m for m in to_compress
                       if estimate_tokens(str(m.get("content", ""))) <= self.oversized_token]
            if trimmed and len(trimmed) < len(to_compress):
                summary = self._try_summarize(trimmed, res.preserved_identifiers)
                if summary:
                    method = "partial_summary"
        if summary is None:
            summary = self._truncate_fallback(to_compress, res.preserved_identifiers)
            method = "truncate"

        summary_msg = {"role": "system",
                       "content": f"【早期对话摘要(已压缩 {len(to_compress)} 条)】\n{summary}"}
        compacted = system_msgs + [summary_msg] + recent
        compacted_tokens = estimate_messages_tokens(compacted)

        if compacted_tokens >= res.original_tokens * 0.9:
            res.messages = list(messages)
            res.method = "skipped_ineffective"
            res.compacted_count = len(messages)
            res.compacted_tokens = res.original_tokens
            sys_tokens = estimate_messages_tokens(system_msgs)
            res.note = (f"压缩跳过:有效压缩空间不足(系统消息占 {sys_tokens} token)。"
                        f"建议精简系统提示或调大上下文预算。")
            return res

        res.messages = compacted
        res.method = method
        res.compacted_count = len(res.messages)
        res.compacted_tokens = compacted_tokens
        return res

    def maybe_compact(self, messages: list) -> tuple:
        """接入用入口:需要压缩才压,返回 (消息列表, CompactionResult | None)。

        与 compact() 的差别:未超预算时不产生 CompactionResult(调用方无需判别),
        跳过/无效时返回**原始消息**而不是半成品;任何异常都降级为原样返回。
        供 agents/base.py::_compact_context 在每次 LLM 调用前挂接。
        """
        try:
            if not self.needs_compaction(messages):
                return list(messages), None
            res = self.compact(messages)
            if res.method == "none" or res.method.startswith("skipped"):
                return list(messages), res
            return res.messages, res
        except Exception:
            return list(messages), None

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
