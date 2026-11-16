"""LlmMixin:经 ModelRouter 的模型调用(线程池异步化 + 流式/注入 opt-in)。

从 `foxclaw/agents/base.py` 搬移出来的 `_call_llm`。
"""
from ._context import _reasoning_inject, _act_stream
from typing import Any


class LlmMixin:
    AGENT_ID: str
    router: Any
    system_prompt: str
    async def _call_llm(
        self,
        messages: list[dict],
        max_tokens: int = 2048,
    ) -> str:
        """通过 ModelRouter 调模型。换模型不影响认知层 —— 体现 LLM 解耦。
        无任何模型 key 时 Router 自动降级 stub,保证系统可独立运行。

        模型调用是同步阻塞的,放到线程池执行,避免堵死 async 事件循环
        (否则模型慢/卡时整个服务无响应)。

        D1 流式(opt-in):`_act_stream` contextvar 被 `run_task_stream` 设置时,改走
        `router.stream` 逐 token 回调(供 SSE 即时出字)+ 累积返回**完整串**(返回值语义与
        非流式**完全一致**,故上层 `_execute` 零感知、零改动)。`stop_event` 串到 stream:
        断连即停生成省 token。流式任何异常 → **fail-safe 降级**为非流式 `complete`。默认
        (contextvar=None)= 走 `complete`,行为逐字不变。

        E2 pre_reasoning 注入:`_reasoning_inject` contextvar 被设置时(钩子注入了上下文),前插
        为一条 user 消息 → 影响本次推理。默认 None 不注入,行为逐字不变。"""
        import asyncio
        inj = _reasoning_inject.get()
        if inj:
            merged = list(messages)
            for i in range(len(merged) - 1, -1, -1):
                if merged[i].get("role") == "user":
                    m = dict(merged[i]); m["content"] = str(inj) + "\n\n" + (m.get("content") or "")
                    merged[i] = m
                    break
            else:
                merged = [{"role": "user", "content": str(inj)}] + merged
            messages = merged
        sc = _act_stream.get()
        # act 路径 LLM 自主工具循环(2026-11-16 接线修复):此前只有 stream_reply
        # 路径挂了 _reply_with_tools,run_task→CognitivePipeline act→_call_llm 走
        # 裸 complete,派发子 Agent(finance/office)拿不到任何工具(T6 真实冒烟
        # 实证:agent 明确报告"会话里没有 web_search")。现与 stream_reply 同一
        # 判定源(FOXCLAW_TOOL_USE=1 且 provider 支持 tools):先走工具循环,
        # 模型自主决策调用/回答;工具步骤经 _record_step 落 task_log.jsonl
        # (evidence 不缺位)。返回 None(provider 不支持/无 schema/stop 置位)→
        # 回落下方 complete,原行为逐字不变。流式路径(_act_stream 置位)不在此
        # 接,保持 stream_reply 既有工具循环。
        if sc is None:
            from ...core.tools import tool_use_enabled as _tool_use_enabled
            if _tool_use_enabled() and self.router.supports_tools():
                _task = next((str(m.get("content") or "") for m in reversed(messages)
                              if m.get("role") == "user"), "")
                try:
                    _final = self._reply_with_tools(_task, messages)
                except Exception:
                    _final = None  # 工具循环任何异常 → 回落普通调用,不阻断任务
                if _final is not None:
                    return _final
        if sc is not None:
            on_token = sc.get("on_token")
            stop_event = sc.get("stop_event")

            def _stream_collect():
                parts = []
                for chunk in self.router.stream(self.system_prompt, messages, max_tokens,
                                                self.AGENT_ID, stop_event=stop_event):
                    parts.append(chunk)
                    if on_token is not None:
                        try:
                            on_token(chunk)
                        except Exception:
                            pass
                return "".join(parts)

            try:
                return await asyncio.to_thread(_stream_collect)
            except Exception:
                pass
        return await asyncio.to_thread(
            self.router.complete,
            self.system_prompt, messages, max_tokens, self.AGENT_ID)
