"""act 路径工具循环线程归属回归(复核者裁定 2026-11-16)。

工具循环是同步阻塞 I/O(模型工具轮 + web_search 网络请求),必须经
asyncio.to_thread 在工作线程执行——阻塞期间事件循环仍可调度其他协程;
返回值语义不变(工具循环结果直接作为 _call_llm 返回,router.complete
不被调用);计量口径零改动(call_tools 的 meter 挂钩不经过本路径)。
"""
import asyncio
import threading
from types import SimpleNamespace

from foxclaw.agents.mixins.llm import LlmMixin


class _LoopAgent(LlmMixin):
    """最小 Agent:工具循环阻塞在工作线程,等待主循环放行。"""

    AGENT_ID = "office"
    system_prompt = "sys"

    def __init__(self, router):
        self.router = router
        self.loop_thread = None
        self.entered = threading.Event()

    def _reply_with_tools(self, task, messages):
        self.loop_thread = threading.get_ident()
        self.entered.set()
        # 模拟真实阻塞(web_search 网络等待):直到主协程放行
        self._release.wait(5)
        return "tool-final"


def test_tool_loop_runs_off_event_loop(monkeypatch):
    monkeypatch.setenv("FOXCLAW_TOOL_USE", "1")
    agent = _LoopAgent(SimpleNamespace(supports_tools=lambda: True))
    agent._release = threading.Event()
    main_thread = threading.get_ident()
    ticks: list = []

    async def main():
        async def heartbeat():
            for _ in range(40):
                ticks.append(threading.get_ident())
                await asyncio.sleep(0.005)

        hb = asyncio.create_task(heartbeat())
        res = await agent._call_llm([{"role": "user", "content": "q"}])
        agent._release.set()
        await hb
        return res

    res = asyncio.run(main())

    assert agent.entered.is_set()       # 工具循环已进入(工作线程)
    assert res == "tool-final"          # 工具循环结果直接返回(语义不变)
    assert agent.loop_thread != main_thread   # 工具循环不在事件循环线程
    assert len(ticks) >= 10             # 工具循环阻塞期间事件循环持续推进
    assert set(ticks) == {main_thread}  # 心跳与主调用同循环(循环未被卡死)
