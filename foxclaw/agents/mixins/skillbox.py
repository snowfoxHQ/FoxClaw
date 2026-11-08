"""SkillBoxMixin —— 技能工具箱:任何 Agent 都能挂载并调用 skill。

原先 `attach_skills / list_skills / use_skill` 只长在 AssistantAgent 身上(助手专属工具箱)。
助手降级为指令技能后,工具箱不能再绑在某个 Agent 上 —— 否则主管/财务/办公全都用不了技能,
而"技能"恰恰是那两个下放的 Agent 的新家。故上移到 mixin,由基类统一混入。
"""

class SkillBoxMixin:
    def attach_skills(self, registry, adapter):
        """挂载 skill 接入层(注册表 + 执行适配器)。"""
        self.skill_registry = registry
        self.skill_adapter = adapter

    def list_skills(self) -> list:
        """列出当前可用的 skill(供 prompt 拼装 / 前端展示)。"""
        reg = getattr(self, "skill_registry", None)
        return reg.catalog() if reg else []

    def use_skill(self, task: str, args: dict = None, confirmed: bool = False) -> dict:
        """按任务匹配 skill 并调用,结果回填 ThoughtChain,过守门人。"""
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
        from ...skills import set_loop_session, reset_loop_session
        from ...core import injection_guard as _ig
        from ._context import _taint_get, _untrusted_get
        token = set_loop_session(self._session_id)
        try:
            # 会话已取回敏感数据(知识库/记忆/网页)后,技能的外发/副作用动作同样要二次确认——
            # 此前技能路径不带污点,permission.evaluate 的 Stage 3.5 只对工具生效。
            # 来源不可信(外部聊天渠道)时按同一口径抬到 TAINT_PERSONAL。
            _taint = _taint_get(self)
            if _untrusted_get(self):
                _taint = max(_taint, _ig.TAINT_PERSONAL)
            result = adp.invoke(skill, args or {}, confirmed=confirmed,
                                taint_level=_taint,
                                taint_threshold=_ig.TAINT_PERSONAL)
        finally:
            reset_loop_session(token)
        self._think(
            content=f"skill 结果:{skill.name} → {result.status} · {result.message[:50]}",
            node_type="reflection", conf=0.88,
        )
        return {"matched": skill.to_dict(), "result": result.to_dict()}
