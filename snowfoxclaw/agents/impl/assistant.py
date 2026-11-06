"""AssistantAgent —— 助手（主管助理）。"""

from ..base import CognitiveAgent
from ._helpers import _fmt_mem
from ...skills import set_loop_session, reset_loop_session

class AssistantAgent(CognitiveAgent):
    AGENT_ID = "assistant"
    AGENT_NAME = "助手 Agent"
    ALLOWED_TOOL_CATEGORIES = {"read", "write", "network"}

    def attach_skills(self, registry, adapter):
        """挂载 skill 接入层:助手的万能工具箱。"""
        self.skill_registry = registry
        self.skill_adapter = adapter

    def list_skills(self) -> list:
        """列出助手当前可用的 skill。"""
        reg = getattr(self, "skill_registry", None)
        return reg.catalog() if reg else []

    def use_skill(self, task: str, args: dict = None, confirmed: bool = False) -> dict:
        """助手按任务匹配 skill 并调用,结果回填 ThoughtChain,过守门人。"""
        reg = getattr(self, "skill_registry", None)
        adp = getattr(self, "skill_adapter", None)
        if not (reg and adp):
            return {"error": "skill 接入层未挂载"}
        # 用升级版 router(三步打分 + 否定词检测),优于老的 reg.match 纯子串匹配
        try:
            from ...skills.pipeline import route as ahs_route
            r = ahs_route(task, reg)
            skill = reg.get(r.primary) if r.primary else None
        except Exception:
            skill = reg.match(task)   # 兜底
        if not skill:
            return {"matched": None, "message": "未匹配到合适的 skill"}
        self._think(
            content=f"匹配 skill:「{skill.name}」({skill.kind}/{skill.side_effect}) 处理:{task[:30]}",
            node_type="tool_intent", tool_name=skill.id, conf=0.9,
        )
        token = set_loop_session(self._session_id)
        try:
            result = adp.invoke(skill, args or {}, confirmed=confirmed)
        finally:
            reset_loop_session(token)
        self._think(
            content=f"skill 结果:{skill.name} → {result.status} · {result.message[:50]}",
            node_type="reflection", conf=0.88,
        )
        return {"matched": skill.to_dict(), "result": result.to_dict()}

    @property
    def _role_prompt(self) -> str:
        return """你是 Snowfoxclaw 的助手 Agent，是主管的直接助理。

你的核心职责：
1. 执行主管分配的协调性任务
2. 整理和汇总来自其他 Agent 的信息
3. 处理跨 Agent 的信息流转
4. 维护用户的个人偏好和工作习惯记忆

你擅长：
- 信息整理与总结
- 跨领域的上下文维护
- 用户意图的精准理解
- 自然、友好的沟通方式

关于联网：你具备实时联网搜索能力——问最新/实时信息时，结果会自动注入上下文或由你调用工具获取；基于检索结果作答，没查到就如实说明，别声称"没有联网能力"。（不编造的通则见内核·真相优先。）"""

    # 生命周期由 base.run_task 模板统一;助手只实现领域执行(默认 entities/recall)。
    async def _execute(self, task, context, session_id, memories):
        self._think(
            content=f"协助处理：{task}",
            node_type="reasoning",
            conf=0.90,
        )

        mem_context = "\n".join([
            _fmt_mem(m) for m in memories[:3]
        ]) if memories else "无相关记忆"

        response = await self._call_llm_web(task, [{
            "role": "user",
            "content": f"请协助处理以下任务：\n{task}\n\n参考记忆：\n{mem_context}"
        }])

        self._think(
            content=f"任务完成：{response[:150]}",
            node_type="decision",
            conf=0.85,
        )

        self._store_memory(
            content=f"协助任务：{task[:100]}\n结果：{response[:200]}",
            mem_type="execution",
        )
        return response, {}

