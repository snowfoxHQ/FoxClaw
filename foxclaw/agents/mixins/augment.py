"""AugmentMixin:能力知识 / 指令技能的上下文增强。

从 `foxclaw/agents/base.py` 搬移出来的增强类方法(默认关,fail-safe)。
"""
import os

from ._context import _taint_raise
from typing import Any, Callable


class AugmentMixin:
    _think: Callable[..., Any]
    available_abilities: Any
    _call_llm: Callable[..., Any]

    def _ability_augment_enabled(self) -> bool:
        """能力知识增强是否启用。默认**关**(behavior-preserving)。
        开启途径:Agent 级 `ABILITY_AUGMENT=True` / 环境变量 `FOXCLAW_ABILITY_AUGMENT=1`。"""
        if getattr(self, "ABILITY_AUGMENT", False):
            return True
        return os.environ.get("FOXCLAW_ABILITY_AUGMENT", "") == "1"

    def _ability_augment(self, query: str, messages: list[dict]) -> list[dict]:
        """【HKC 第2档 Step3·能力→上下文注入】若启用且有 coverage 达标的能力与任务相关,
        用 HKC 检索该能力 domain 下与任务相关的知识,注入 messages 前(增强回答)。

        **默认关 → 原样返回(行为逐字不变)**。无达标能力/无匹配/HKC 不可用/任何异常 → 原样返回
        (fail-safe:增强绝不影响主流程)。只增强上下文,不改路由/不触发副作用。"""
        if not self._ability_augment_enabled():
            return messages
        try:
            suff = [a for a in self.available_abilities() if a.get("sufficient")]
            if not suff:
                return messages
            best = self._match_ability(query, suff)
            if best is None:
                return messages
            caps = getattr(self, "_caps", None)
            hkc = caps.get("hkc") if caps is not None else None
            if hkc is None or not getattr(hkc, "available", False):
                return messages
            hits = hkc.client.search(query, mode="hybrid", top_k=5,
                                     domain=best.get("domain", ""))
            if not hits:
                return messages
            lines = "\n".join(f"- {h.name}: {h.summary}" for h in hits[:5])
            from ...core import injection_guard as _ig
            ctx = (f"【能力知识增强 · {best.get('display_name') or best['ability_key']}】"
                   f"以下是知识库中与本任务相关、且该能力已达覆盖的知识,供参考:\n"
                   + _ig.wrap_untrusted(lines, source="knowledge"))
            self._think(content=f"能力增强「{best['ability_key']}」注入 {len(hits)} 条知识",
                        node_type="tool_use", conf=0.85)
            _taint_raise(self, _ig.taint_level("knowledge"))
            return [{"role": "user", "content": ctx}] + messages
        except Exception:
            return messages

    def _skill_augment(self, query: str, messages: list[dict]) -> list[dict]:
        """按触发词匹配 SKILL.md 指令技能,把正文注入(合并进最后一条 user 消息)。"""
        try:
            reg = getattr(self, "md_skills", None)
            if not reg:
                return messages
            matched = reg.match(query)[:3]
            if not matched:
                return messages
            ctx = "以下是可参照的操作指令(SKILL),请据此执行:\n\n" + \
                  "\n\n---\n\n".join(f"## {s.name}\n{s.body}" for s in matched)
            self._think(content=f"注入 {len(matched)} 条指令技能",
                        node_type="tool_use", conf=0.85)
            merged = list(messages)
            for i in range(len(merged) - 1, -1, -1):
                if merged[i].get("role") == "user":
                    m = dict(merged[i]); m["content"] = ctx + "\n\n" + (m.get("content") or "")
                    merged[i] = m
                    return merged
            return [{"role": "user", "content": ctx}] + merged
        except Exception:
            return messages

    @staticmethod
    def _match_ability(query: str, abilities: list):
        """把任务匹配到最相关的达标能力:任务与能力(display_name/domain/coverage 概念键)的
        关键词子串重叠最高者(与 HKC coverage 同款子串匹配精神)。无重叠 → None(不注入无关知识)。"""
        q = (query or "").lower()
        best, best_score = None, 0
        for a in abilities:
            terms = [str(a.get("display_name", "")), str(a.get("domain", "")),
                     *[str(k) for k in (a.get("coverage") or {}).keys()]]
            score = sum(1 for t in terms if t and t.lower() in q)
            if score > best_score:
                best, best_score = a, score
        return best

    async def _call_llm_web(self, query: str, messages: list[dict], max_tokens: int = 2048) -> str:
        """带能力知识 + 指令技能增强的 LLM 调用(名称沿用历史,已不含联网)。
        增强是同步阻塞 I/O(HKC 检索),放到线程池执行,避免堵死 async 事件循环。"""
        import asyncio
        aug = await asyncio.to_thread(self._ability_augment, query, messages)
        aug = await asyncio.to_thread(self._skill_augment, query, aug)
        return await self._call_llm(aug, max_tokens=max_tokens)
