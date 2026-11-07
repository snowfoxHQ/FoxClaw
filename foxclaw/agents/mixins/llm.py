"""LlmMixin:经 ModelRouter 的模型调用(线程池异步化 + 流式/注入 opt-in)。

从 `foxclaw/agents/base.py` 搬移出来的 `_call_llm`。
"""
from ._context import _reasoning_inject, _act_stream


class LlmMixin:
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
            # 合并进**最后一条 user 消息**(与 _web_augment 同法),而非前插独立 user 消息——
            # 后者会产生连续两条 user,某些 OpenAI-compatible 端点会拒绝,违反「模型无关」铁律
            # (B4 审核 A3)。拷贝后改,不动原 messages。
            merged = list(messages)
            for i in range(len(merged) - 1, -1, -1):
                if merged[i].get("role") == "user":
                    m = dict(merged[i]); m["content"] = str(inj) + "\n\n" + (m.get("content") or "")
                    merged[i] = m
                    break
            else:
                merged = [{"role": "user", "content": str(inj)}] + merged   # 无 user 消息才独立前插
            messages = merged
        sc = _act_stream.get()
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
                            pass          # 单个 token 回调故障不中断收集
                return "".join(parts)

            try:
                return await asyncio.to_thread(_stream_collect)
            except Exception:
                pass                      # 流式整体故障 → 落到下方非流式 complete(fail-safe)
        return await asyncio.to_thread(
            self.router.complete,
            self.system_prompt, messages, max_tokens, self.AGENT_ID)
