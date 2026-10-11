"""ToolExecMixin:LLM 自主工具选择的分发执行 + 工具调用循环。

从 `hestia/agents/base.py` 搬移出来的工具执行相关方法。
"""
from ._context import _taint_get, _taint_raise


class ToolExecMixin:
    def _dispatch_tool(self, name: str, args: dict) -> str:
        """执行一个被 LLM 选中的工具,返回结果文本(注回模型)。

        动作级安全阀(真正的兜底,不指望 prompt 隔离挡住弱模型):
          ① per-tool deny 闸(kill-switch);
          ② 未知工具 → 不执行;
          ③ **四阶段权限管线**(permission.evaluate):按能力分级裁决 allow/ask/deny——
             只读放行;写/发送/不可逆/外部副作用/未声明的新工具 → **人在回路(HITL)**,只出草稿不自动执行;
          ④ web_search 的个人数据否决(私人问题不外发)。
        任何异常不外泄、如实降级。"""
        import types as _types
        from ...core import tools as _tools
        from ...core import injection_guard as _ig
        from ...skills import permission as _perm
        if _tools.is_tool_denied(name):
            return f"(工具 {name} 已被策略禁用,未执行)"
        if not _tools.default_registry().has(name):
            return f"(未知工具 {name},未执行)"
        # ── 参数校验(用注册表里同一份 JSON Schema)────────────────────
        # 修复前:schema 只发给模型,执行侧不校验 → 模型给错类型/漏参数会直接流到
        # 下游。且下方 permission Stage 1 因 params={} 恒不触发,只靠手工 if-not 兜。
        # 新增校验闸后,错误**回注模型**让其自修正,而不是静默用空参数继续
        # (那会让模型误以为工具已执行,进而编造基于"工具结果"的结论)。
        _reg = _tools.default_registry()
        _errs = _reg.validate_args(name, args or {})
        if _errs:
            self._think(content=f"工具 {name} 入参校验失败: {'; '.join(_errs)[:120]}",
                        node_type="tool_intent", conf=0.9)
            return (f"(工具 {name} 入参不合法: {'; '.join(_errs)}。"
                    f"请按工具声明修正参数后重试。)")
        # ── 权限管线:动作级闸门。构造 tool-spec 交四阶段裁决 ──
        cap = _tools.tool_capability(name)
        # 传入真实 params schema,使 permission Stage 1(必填校验)真正生效,
        # 不再依赖下游手工检查兜底。兼容 permission 的两种 params 格式。
        _schema = _reg.param_schema(name)
        _req = set(_schema.get("required") or [])
        _params = {k: {"required": k in _req, "type": v.get("type")}
                   for k, v in (_schema.get("properties") or {}).items()
                   if isinstance(v, dict)}
        spec = _types.SimpleNamespace(id=f"tool:{name}", params=_params, **cap)
        decision = _perm.evaluate(spec, args or {}, confirmed=False,
                                  taint_level=_taint_get(self),
                                  taint_threshold=_ig.TAINT_PERSONAL)
        if decision.decision == "deny":
            return f"(工具 {name} 被权限策略拒绝:{decision.reason},未执行)"
        if decision.decision == "ask":
            # 红线/副作用/未声明工具 → 人在回路:不自动执行,交回由用户确认(KERNEL §6 同旨)
            self._think(content=f"工具 {name} 需用户确认({decision.reason}),已暂停执行",
                        node_type="decision", conf=0.9)
            return (f"(工具 {name} 属于需确认的动作:{decision.reason}。按安全策略我不会自动执行——"
                    f"这类涉及发送/转账/删除/修改/对外调用的红线动作只能由你亲自确认后进行;"
                    f"我可以先把要做的操作拟成草稿供你审。)")
        try:
            if name == "web_search":
                from ...core import web_search as _ws
                q = str((args or {}).get("query", "")).strip()
                if not q:
                    return "(web_search 缺少 query 参数)"
                if _ws.is_personal_query(q):   # 私人账本/行程/记忆 → 绝不外发,让模型用本地信息答
                    return "(该问题属于用户本地数据,已跳过联网;请依据本地领域信息回答)"
                payload = _ws.search_answer(q)
                if not payload or (not payload.get("answer") and not payload.get("references")):
                    return ("联网搜索未返回结果(可能未配置搜索服务或网络不可用);"
                            "请如实告知用户暂时无法查询实时信息,切勿编造版本号/新闻/价格等。")
                self._think(content=f"工具 web_search「{q}」命中", node_type="tool_use", conf=0.9)
                _taint_raise(self, _ig.taint_level("web_search"))   # 外部不可控数据 → 会话污点
                return _ws.format_for_prompt(q, payload)
            if name == "recall_memory":
                q = str((args or {}).get("query", "")).strip()
                # top_k 此前硬编码 5,模型传值无效 → 加边界校验后尊重模型意图
                # (schema 已声明 top_k,1-20;越界回落默认,防一次召回拉爆上下文)
                try:
                    top_k = max(1, min(20, int((args or {}).get("top_k", 5))))
                except Exception:
                    top_k = 5
                res = self._recall_memory(q, top_k=top_k) if q else []
                if not res:
                    return "(未召回到相关记忆)"
                lines = "\n".join(f"- {r.get('content', '')}" for r in res[:top_k])
                # 记忆可能被投毒(此前注入固化) → 当作不可信数据回注,防记忆投毒二次生效
                _taint_raise(self, _ig.taint_level("memory"))   # 个人记忆 → 会话污点(高敏感)
                return "【召回的相关记忆】\n" + _ig.wrap_untrusted(lines, source="memory")
        except Exception as e:
            return f"(工具 {name} 执行异常:{type(e).__name__})"
        return f"(未知工具 {name},未执行)"

    def _reply_with_tools(self, task: str, messages: list, stop_event=None, max_rounds: int = 4):
        """LLM 自主工具调用循环(取代'调 LLM 前用关键词猜要不要联网/召回')。
        返回最终答案文本;若 provider 实际不支持 tools → 返回 None(上层回退关键词增强链)。
        工具轮用非流式解析,最终文本由上层 chunk 输出。"""
        from ...core import tools as _tools
        if not self.router.supports_tools():
            return None
        schemas = _tools.default_registry().schemas()
        if not schemas:
            return None
        conv = list(messages)
        for _ in range(max_rounds):
            if stop_event is not None and stop_event.is_set():
                return None                      # 断连 → None(回退;fallback 也会因 stop 立即终止)
            res = self.router.call_tools(self.system_prompt, conv, schemas,
                                         agent_id=self.AGENT_ID)
            if not res.get("supported"):
                return None                      # 端点实际不支持 tools → fallback
            calls = res.get("tool_calls") or []
            if not calls:
                return res.get("text") or None   # 模型直接给出最终答案;空答 → None → fallback(不再空回复不回退)
            conv.append(res["assistant_msg"])
            for c in calls:
                result = self._dispatch_tool(c.get("name", ""), c.get("args") or {})
                conv.append(self.router.format_tool_result(c, result))
        # 轮数超限 → 强制不再调工具、收敛出文本答案
        final = self.router.call_tools(self.system_prompt, conv, schemas,
                                       tool_choice="none", agent_id=self.AGENT_ID)
        if not final.get("supported"):
            return None                          # 收敛调用失败 → fallback(不再空回复不回退)
        return final.get("text") or None         # 收敛出空答 → None → fallback
