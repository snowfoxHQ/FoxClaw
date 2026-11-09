"""
CognitiveAgent — 认知增强 Agent 基类
内建 HMR + ThoughtChain + Harness，不需要外部配置
每个 Agent 实例拥有：
  - 独立的 HMR Workspace（记忆隔离）
  - 独立的 ThoughtChain（推理轨迹）
  - 独立的 Harness 实例（策略边界）
  - 共享的 Anthropic LLM 接口
"""

import threading
import uuid

from ..core.thought_chain import ThoughtChain, ThoughtNode
from ..harness.harness import Harness, HarnessResult

# 工具并行执行后,多个 worker 线程可能同时走 _think(web_search 命中 / 记忆召回节点),
# 而 _parent_node_id 是共享可变指针。用模块级 RLock 串行化「建链 + 挪指针」两步,
# 保证 thought 链仍是一条合法链(节点本身顺序由完成时刻决定,语义可接受)。
_THINK_LOCK = threading.RLock()

# ── S3 步骤 2：按职责拆分到 mixin（纯搬移、零行为变更）────────────────────────
# 会话状态基础设施（contextvar + 污点 + HarnessBlockedError）搬至 mixins/_context.py；
# 各职责方法搬至 mixins/{llm,augment,memory,preference,prompt,tool_exec}.py；
# 主循环（会话管理 / _think / stream_reply / run_task 生命周期）留在本文件。
from .mixins._context import (
    _agent_ctx_get,
    _agent_ctx_set,
    _taint_get,
    _taint_raise,
    _taint_reset,
    _untrusted_set,
    _evidence_reset,
    _evidence_take,
    _suppress_memory,
    _act_stream,
    _reasoning_inject,
    _resume_steps,
    HarnessBlockedError,
)
from .mixins.augment import AugmentMixin
from .mixins.llm import LlmMixin
from .mixins.memory import MemoryMixin
from .mixins.skillbox import SkillBoxMixin
from .mixins.preference import PreferenceMixin
from .mixins.prompt import PromptMixin, KERNEL_PROMPT
from .mixins.tool_exec import ToolExecMixin

# 历史导入兼容：`from foxclaw.agents.base import X` 的 X 在此 re-export（测试/外部模块仍按此路径取符号）。
__all__ = [
    "CognitiveAgent", "HarnessBlockedError", "KERNEL_PROMPT",
    "_suppress_memory", "_act_stream", "_taint_get", "_taint_raise", "_taint_reset",
]


def _plan_payload(ctx) -> dict | None:
    """从管线 ctx 提取计划负载:{steps, flow, multi_step};没有计划 → None。

    领域管线把计划写 `ctx.plan`(pipeline.stage_plan),主管管线写 `ctx.plan_obj`
    (pipeline.supervisor_stages.sv_plan),两种形态都兼容 → 两条链路都能回显计划。
    纯只读、不抛异常(坏了就当没有计划,不拖垮任务)。
    """
    for attr in ("plan", "plan_obj"):
        p = getattr(ctx, attr, None)
        if p is None:
            continue
        try:
            steps = p.get("steps") if isinstance(p, dict) else getattr(p, "steps", None)
            if not steps:
                continue
            steps = [str(s) for s in steps]
            flow = (p.get("flow", "single") if isinstance(p, dict)
                    else getattr(p, "flow", "single")) or "single"
            multi = (p.get("multi_step") if isinstance(p, dict)
                     else getattr(p, "multi_step", False))
            return {"steps": steps, "flow": flow,
                    "multi_step": bool(multi) or len(steps) > 1}
        except Exception:
            continue
    return None


class CognitiveAgent(AugmentMixin, LlmMixin, MemoryMixin,
                     PreferenceMixin, PromptMixin, ToolExecMixin, SkillBoxMixin):
    """
    所有 Foxclaw Agent 的基类
    子类只需实现：_role_prompt、allowed_tools、run_task
    (system_prompt 由基类统一 = KERNEL_PROMPT 内核前缀 + 子类 _role_prompt 角色职责)
    """

    AGENT_ID: str = "base"
    AGENT_NAME: str = "Base Agent"
    ALLOWED_TOOL_CATEGORIES: set[str] = {"read"}

    def __init__(
        self,
        hmr,                          # HMR 实例（从外部注入）
        thought_chain: ThoughtChain,
        client=None,
        data_dir: str = "./foxclaw_data",
        shadow_rounds: int = 20,
        router=None,                  # ModelRouter（多模型路由,从外部注入）
        spine=None,                   # 【P0】全局 EventSpine（harness 事件汇入脊柱→统一 WS 投影）
        caps=None,                    # 【P1-3】CapabilityRegistry（按名解析同伴,不再直接互持）
    ):
        self.hmr = hmr
        self.thought_chain = thought_chain
        self.client = client
        # 多模型路由:优先用注入的 router;否则惰性创建(自动探测可用模型)
        if router is None:
            from ..core.model_router import ModelRouter
            router = ModelRouter()
        self.router = router
        self.data_dir = data_dir

        # 独立 Harness
        self.harness = Harness(
            agent_id=self.AGENT_ID,
            thought_chain=thought_chain,
            policy_path=f"{data_dir}/policy_{self.AGENT_ID}.yaml",
            shadow_rounds=shadow_rounds,
            spine=spine,
        )
        # 调度层(主管)豁免语义漂移拦截:接收任意输入是其本职,不算异常。
        # 领域 Agent 保持语义拦截:执行业务时跑题(如财务去写诗)才该拦。
        self.harness.block_on_semantic = not getattr(self, "IS_ROUTER", False)

        # 获取或创建 HMR Workspace
        self.workspace = hmr.get_workspace(self.AGENT_ID)

        # 每任务会话状态改由 contextvars 承载(见文件顶部)。这里只需一个 per-实例 token
        # 作为分槽 key;_session_id / _parent_node_id 是路由到 contextvar 的 property,
        # 故 start_session / _think 里既有的读写代码完全无需改动。
        self._ctx_token = uuid.uuid4().hex

        # 【P1-3】能力注册中枢:Agent 不再直接持有 search_runtime,而是按名惰性解析
        # (打断 agents⇄search_runtime⇄agent_port⇄agents 的直接互持环)。
        self._caps = caps

    def search_internal(self, query: str, top_k: int = 5):
        """Agent 反向调用内部搜索(HMR记忆 + HKC知识 + Agent态势),只查内部、不联网。
        这是「串连 Agent」双向连通的 Agent→搜索 方向。经注册中枢惰性解析 search_runtime。"""
        caps = getattr(self, "_caps", None)
        rt = caps.get("search_runtime") if caps is not None else None
        if rt is None:
            return None
        return rt.search(query, agent_id=self.AGENT_ID, internal_only=True)

    def available_abilities(self, keys: list | None = None) -> list:
        """【HKC 第2档 Step2·能力自省】证据驱动地查 HKC 的 ACE 能力:哪些的知识覆盖已达标
        (可编译 sufficient=True)、哪些还差(sufficient=False + missing_skills)。

        **只读、informational——不改任何执行**。HKC 未启用/未 vendor → []。coverage 是确定性
        (无 LLM),但对每个能力算一遍图覆盖,故**按需调用**(非热路径)。注:HKC 知识是全局共享
        的(非按 Agent 分区),故各 Agent 看到的能力覆盖一致——这是"当前知识能支撑哪些能力"的
        系统级自省,挂在 Agent 上是"我能取用哪些能力"的语义。"""
        caps = getattr(self, "_caps", None)
        hkc = caps.get("hkc") if caps is not None else None
        if hkc is None or not getattr(hkc, "available", False):
            return []
        client = hkc.client
        try:
            all_keys = list(keys) if keys else list(client.list_abilities())
        except Exception:
            return []
        out = []
        for key in all_keys:
            try:
                ab = client.compile_ability(key)
                out.append({"ability_key": ab.ability_key, "display_name": ab.display_name,
                            "domain": ab.domain, "sufficient": True, "coverage": ab.coverage})
            except Exception as e:
                if hasattr(e, "missing_skills"):     # 覆盖不足=证据 gate(非错误)
                    out.append({"ability_key": key, "sufficient": False,
                                "coverage": getattr(e, "coverage", {}),
                                "missing_skills": getattr(e, "missing_skills", [])})
                # 其它异常(未知 key/图错误)→ 跳过该能力,不污染结果
        return out

    # ── 每任务会话指针(contextvar 支撑,property 透明路由)──────
    @property
    def _session_id(self):
        return _agent_ctx_get(self._ctx_token).get("session_id")

    @_session_id.setter
    def _session_id(self, value):
        _agent_ctx_set(self._ctx_token, session_id=value)

    @property
    def _parent_node_id(self):
        return _agent_ctx_get(self._ctx_token).get("parent_node_id")

    @_parent_node_id.setter
    def _parent_node_id(self, value):
        _agent_ctx_set(self._ctx_token, parent_node_id=value)

    # ── 会话管理 ───────────────────────────────────────────

    def start_session(self, goal: str, entities: list[str] | None = None) -> str:
        """开始一个新任务会话，注册目标基线"""
        self._session_id = f"sess_{uuid.uuid4().hex[:10]}"
        self._parent_node_id = None
        _taint_reset(self)   # 新会话从无污点起算(数据敏感度清零)
        _evidence_reset(self)  # 新会话不继承上一个任务的执行证据(否则证据串台)

        # 注册 Harness 目标基线
        self.harness.register_goal(
            goal_text=goal,
            allowed_tool_categories=self.ALLOWED_TOOL_CATEGORIES,
            baseline_entities=set(entities or []),
        )

        # 在 ThoughtChain 里记录目标基线节点
        goal_node = ThoughtNode.create(
            session_id=self._session_id,
            agent_id=self.AGENT_ID,
            type="goal",
            content=goal,
            conf=1.0,
            entities=entities or [],
        )
        self.thought_chain.add_node(goal_node)
        self.thought_chain.update_harness(goal_node.id, "BASELINE")
        self._parent_node_id = goal_node.id

        # 【深审修复·移除惰性共享写】原先 `self.workspace.active_goal = goal` 把每任务目标
        # 写到 per-Agent **共享**的 HMR workspace 上,同一 Agent 实例并发多任务会互相覆盖。
        # 审计确认此写**完全惰性**:foxclaw 召回走显式 query(见 _recall_memory,从不读
        # workspace.active_goal),save_workspace 从未被调用(不持久化),无测试依赖它——
        # 故 race 零可观测影响。移除该误导性共享写:**每任务目标的权威来源**是会话隔离的
        # ThoughtChain(goal 节点,session_id 经 contextvar 隔离)+ 事件驱动的 CognitiveState
        # (execution.completed 的 active_goal)。共享单值 workspace 本就无法忠实承载 N 个并发目标。
        return self._session_id

    # ── ThoughtChain + Harness 集成推理步骤 ───────────────

    def _reasoning_result(self, node):
        """经统一 PolicyGate 做推理准入,返回 HarnessResult(与直接 harness.check 逐字一致)。
        gate 经能力中枢惰性解析并缓存;无 gate(standalone/治理场景)则直接 harness.check。"""
        gate = getattr(self, "_gate_cache", "unset")
        if gate == "unset":
            caps = getattr(self, "_caps", None)
            gate = caps.get("policy_gate") if caps is not None else None
            self._gate_cache = gate
        if gate is not None:
            return gate.check_reasoning(self.harness, node).raw
        return self.harness.check(node)

    def _think(
        self,
        content: str,
        node_type="reasoning",
        conf: float = 0.9,
        mem_refs: list[str] | None = None,
        tool_name: str | None = None,
        entities: list[str] | None = None,
        metadata: dict | None = None,
    ) -> tuple[ThoughtNode, HarnessResult]:
        """
        记录一个推理步骤，经过 Harness 校验
        如果 BLOCK，抛出 HarnessBlockedError
        """
        node = ThoughtNode.create(
            session_id=self._session_id or "no_session",
            agent_id=self.AGENT_ID,
            type=node_type,
            content=content,
            conf=conf,
            mem_refs=mem_refs or [],
            parent_ids=[self._parent_node_id] if self._parent_node_id else [],
            tool_name=tool_name,
            entities=entities or [],
            metadata=metadata,
        )
        # 锁只护「链插入 + 父指针挪动」这两步共享状态;Harness 判定在锁外,避免钩子阻塞并发工具。
        with _THINK_LOCK:
            self.thought_chain.add_node(node)

        # Harness 校验(经统一 PolicyGate 做推理准入;拿到的仍是同一 HarnessResult,行为一致)
        result = self._reasoning_result(node)

        if result.is_blocked:
            raise HarnessBlockedError(result, node)

        # WARN：降低置信度但继续
        if result.is_warned:
            node.conf = max(node.conf - 0.15, 0.1)

        with _THINK_LOCK:
            self._parent_node_id = node.id
        return node, result

    @staticmethod
    def _sanitize_history(history, max_turns: int = 12):
        """清洗前端传来的多轮历史 → 合法 messages:只留 {role∈user/assistant, content:非空str},
        截到最近 max_turns 条。防注入非法角色/超长上下文。"""
        out = []
        for m in (history or [])[-max_turns:]:
            if not isinstance(m, dict):
                continue
            role, content = m.get("role"), m.get("content")
            if role in ("user", "assistant") and isinstance(content, str) and content.strip():
                out.append({"role": role, "content": content})
        return out

    def _compact_context(self, messages: list, stage: str = "") -> list:
        """LLM 调用前的上下文预算闸:超阈值则摘要/截断,任何失败都原样返回。

        历史:`core/compaction.py` 的 Compactor 早已实现(含 tool_calls 原子组切分、
        LLM 摘要→部分摘要→截断三级降级),但**没有任何生产调用点**,实际生效的
        只有 `_sanitize_history` 的「按条数砍到 12 条」——长对话、多工具轮会
        直接把 provider 上下文撑爆。这里补上真实接线。

        预算由 FOXCLAW_CONTEXT_MAX_TOKENS 控制(默认 6000);置 0 可整体关闭。
        """
        import os as _os
        try:
            limit = int(_os.environ.get("FOXCLAW_CONTEXT_MAX_TOKENS", "6000") or 6000)
        except Exception:
            limit = 6000
        if limit <= 0:
            return messages
        try:
            from ..core.compaction import Compactor
            compactor = Compactor(router=getattr(self, "router", None), max_tokens=limit)
            compacted, res = compactor.maybe_compact(messages)
            if res is not None:
                try:
                    self._think(
                        content=(f"上下文压缩[{stage or 'llm'}] {res.method}: "
                                 f"{res.original_count}→{res.compacted_count} 条, "
                                 f"{res.original_tokens}→{res.compacted_tokens} token"
                                 + (f"; {res.note}" if res.note else "")),
                        node_type="reflection", conf=0.7)
                except Exception:
                    pass
            return compacted
        except Exception:
            return messages

    @staticmethod
    def _attachments_context(attachments) -> str:
        """把聊天附件拼成一段给 LLM 的上下文:文本类贴正文、其它降级为「文件名 + 元数据」提示。

        附件正文已在路由层 _sanitize_attachments 清洗(限长/限量);这里再按**不可信数据**隔离:
        附件可能是别人转发的文件,与用户当面对话不是同一信任级别,而手写中文定界符可被正文
        伪造。统一走 wrap_untrusted(随机 nonce 边界 + 破界 defang + 注入特征告警)。"""
        if not attachments:
            return ""
        from ..core.injection_guard import wrap_untrusted
        parts = []
        for a in attachments:
            if not isinstance(a, dict):
                continue
            name = a.get("name") or "文件"
            text = (a.get("text") or "").strip()
            if text:
                block = wrap_untrusted(text, source="attachment")
                parts.append(f"【附件「{name}」内容如下】\n{block}\n【附件「{name}」结束】")
            else:
                mime = a.get("mime") or "未知类型"
                size = a.get("size") or 0
                parts.append(f"【附件「{name}」({mime}, {size} 字节)】用户上传了此文件,"
                             f"但无法提取为可读文本;若需要其内容,请说明并提示用户改用文本/PDF 等可读格式。")
        return "\n\n" + "\n\n".join(parts) if parts else ""

    def stream_reply(self, task: str, stop_event=None, images: list | None = None, history: list | None = None,
                     attachments: list | None = None, on_thinking=None, on_evidence=None):
        """流式直答路径(方案A):对话/问答/分析,不做多步编排(那走 run_task)。同步生成器,
        由端点在线程池迭代成 SSE。stop_event 置位(客户端断连)则提前终止,省 token。
        images(可选)= 多模态图片,随问题发给视觉模型(模型无关,非视觉模型 router 层降级)。
        history(可选)= 前端带来的最近几轮对话 → 拼在当前问题前,让多轮连贯。
        attachments(可选)= 聊天附件 [{name,size,mime,url,text}]:text 为提取正文,拼进问题末尾
          供 LLM 阅读;text 为空的(二进制/图片)降级为「文件名 + 元数据」提示。
        on_thinking(可选)= 推理增量回调。模型的原生 reasoning 与正文内嵌 <think> 块都经此
          出去,由端点作为独立 SSE `thinking` 事件送前端折叠显示;正文 (yield) 仍走净化。
        on_evidence(可选)= 执行证据回调。工具轮的每步核验结论(verified/unverified/failed)
          + 可核对凭据(来源链接、真实存在的文件)在本轮结束时一次性送出,由端点作为
          SSE `evidence` 事件给前端展示 —— 「做成了」这句话从此可以点开看凭什么。

        两条路径(单一决策源之争,见 TOOL_USE_PLAN.md):
          ① FOXCLAW_TOOL_USE=1 且 provider 支持 tools 且无图 → **LLM 自主工具循环**(_reply_with_tools),
             最终答案 chunk 输出;联网/召回由模型自己决策,不再靠关键词表。
          ② 否则(默认 / 不支持 tools / 带图)→ **现有关键词增强链 + 真流式**(fallback,行为不变)。"""
        from ..core import tools as _tools
        from ..core import injection_guard as _ig
        from ..core.model_router import _chunk_text
        task = str(task or "")
        _evidence_reset(self)      # 每轮从零开账:上一轮的证据不能冒充这一轮
        if attachments:
            ctx = self._attachments_context(attachments)
            if ctx:
                task = task + ctx
        hist = self._sanitize_history(history)
        messages = hist + [{"role": "user", "content": task}]
        parts = []
        used_tools = False
        guarded_out = False                       # 输出护栏是否触发(触发的污染回复不落库,防回流)

        def _emit_thinking(delta: str) -> None:
            """推理内容的最后一道关:思考也不许带密钥/系统提示原文(与正文同等级净化)。"""
            if not delta or on_thinking is None:
                return
            try:
                if _ig.scan_output(delta):
                    on_thinking("(推理内容已被安全护栏过滤)")
                    return
                delta = _ig.redact_secrets(delta)
            except Exception:
                pass
            try:
                on_thinking(delta)
            except Exception:
                pass                         # 前端断连/回调异常不得影响正文生成

        if _tools.tool_use_enabled() and self.router.supports_tools() and not images:
            # 与 fallback 一致地注入偏好/技能/能力(否则开工具化会静默丢掉"总是注入偏好"的保证、
            # 丢掉 SKILL.md 与能力知识)。**唯独不做 web_augment**——联网交给 LLM 自主调 web_search 工具。
            try:
                tmsgs = self._ability_augment(task, messages)
                tmsgs = self._skill_augment(task, tmsgs)
                tmsgs = self._pref_augment(tmsgs)
            except Exception:
                tmsgs = messages
            _compact = getattr(self, "_compact_context", None)   # 轻量替身对象无此方法
            if _compact is not None:
                tmsgs = _compact(tmsgs, "tools")
            try:
                final = self._reply_with_tools(task, tmsgs, stop_event=stop_event)
            except Exception:
                final = None
            if final is not None:                # None = 端点不支持 → 落到 fallback
                # 工具路径有完整文本 → 流出前兜底(密钥脱敏/拦系统提示 dump);传 kernel 用逐字串比对更准
                final, _oiss = _ig.guard_output(final, kernel=KERNEL_PROMPT)
                guarded_out = bool(_oiss)
                used_tools = True
                for chunk in _chunk_text(final):
                    if stop_event is not None and stop_event.is_set():
                        break
                    parts.append(chunk)
                    yield chunk
        if not used_tools:                        # fallback:关键词增强链 + 真流式(行为逐字不变)
            try:
                messages = self._web_augment(task, messages)   # 增强都合并进"最后一条 user",历史不受影响
                messages = self._ability_augment(task, messages)
                messages = self._skill_augment(task, messages)
                messages = self._pref_augment(messages)
            except Exception:
                messages = hist + [{"role": "user", "content": task}]
            _compact = getattr(self, "_compact_context", None)   # 轻量替身对象无此方法
            if _compact is not None:
                messages = _compact(messages, "chat")
            _tail = ""   # 滑窗尾缓冲:跨 chunk 边界的泄露检测只扫最近 _WIN 字,不再整段重扫(消 O(n²))
            _WIN = 256   # 覆盖最长敏感串(密钥)跨 chunk 边界的情况
            for chunk in self.router.stream(self.system_prompt, messages,
                                            agent_id=self.AGENT_ID, stop_event=stop_event,
                                            images=images,
                                            on_thinking=_emit_thinking if on_thinking else None):
                # 流出前检测(而非"yield 后才检测"):密钥/泄露所在 chunk 不再已发给用户才中断。
                # 只扫"尾缓冲 + 新 chunk"(增量滑窗),命中则本 chunk 不发、整段 dump 止于此。
                _probe = _tail + chunk
                if _ig.scan_output(_probe):
                    guarded_out = True
                    break
                _tail = _probe[-_WIN:]
                parts.append(chunk)
                yield chunk
            if guarded_out:
                _notice = "\n\n(⚠ 已触发输出安全护栏,中断后续内容。)"
                parts.append(_notice)
                yield _notice
        reply = "".join(parts)
        stopped = stop_event is not None and stop_event.is_set()
        # 执行证据在正文之后一次性上报(晚于 delta、早于 done):前端拿到时答案已完整,
        # 可以挂在答案下方供人核对;没有工具步的纯对话不产生任何证据事件。
        if on_evidence is not None:
            try:
                _evs = _evidence_take(self)
                if _evs:
                    on_evidence(_evs)
            except Exception:
                pass                       # 证据上报失败不得影响已生成的回答
        # 落库前最终兜底(仅 fallback 路径需要):fallback 增量检查(无 kernel、只抓明显 dump)可能漏过
        # 逐字复现的系统提示,这里用 kernel 逐字串比对再判一次,命中则不落库(防污染回流),不影响已流给
        # 用户的内容。工具路径在流出前已 guard_output(kernel) 完整防护,无需重复扫(消冗余)。
        if reply and not guarded_out and not used_tools and _ig.scan_output(reply, kernel=KERNEL_PROMPT):
            guarded_out = True
        try:
            self._think(content=("流式直答:" + reply)[:500], node_type="reflection", conf=0.8)
        except Exception:
            pass
        # 活动可观测:对话路径此前**不发任何事件** → /bus/history 恒空、前端"活动"页永远没内容
        # (run_task 走 _emit_execution 会发,但直答/流式对话不经那条路)。这里补一条同源事件。
        try:
            _caps = getattr(self, "_caps", None)
            _spine = _caps.get("event_spine") if _caps is not None else None
            if _spine is not None and reply:
                _spine.publish("chat.completed", {
                    "agent_id": self.AGENT_ID,
                    "task": (task or "")[:120],
                    "reply_chars": len(reply),
                    "guarded": bool(guarded_out),
                }, source=self.AGENT_ID)
        except Exception:
            pass
        # 写入记忆(与 /api/task 一致;MCP 场景由 _suppress_memory 兜住);断连中止/触发输出护栏则不写
        #
        # A 类修复:故障降级回复**禁止写入长期记忆** —— 把「模型失败/未执行」当成
        # 「对话结论」沉淀,会让 HMR 后续召回持续复读故障文案(一次故障 → 长期污染)。
        # 判据用 take_call_degraded()(per-call)而非全局 is_stub:is_stub=True 是
        # 「从未配置模型」的离线态,那个场景下记录任务/占位仍有诊断价值,不该一刀切。
        _degraded = False
        try:
            _degraded = bool(self.router.take_call_degraded())
        except Exception:
            _degraded = False
        if reply and not stopped and not guarded_out and not _degraded:
            try:
                safe_reply = _ig.redact_secrets(reply)   # 落库同样脱敏,防密钥沉进长期记忆
                self._store_memory(f"对话:{task} → {safe_reply}"[:800], mem_type="conversation")
            except Exception:
                pass
            self._maybe_store_preference(task)   # 对话里的偏好表达 → 持久记(preference 类型)
        elif _degraded and reply and not stopped:
            # 降级:只留事件痕迹(可观测、可归因),不写记忆内容
            try:
                _spine = _caps.get("event_spine") if _caps is not None else None
                if _spine is not None:
                    _spine.publish("chat.degraded", {
                        "agent_id": self.AGENT_ID,
                        "task": (task or "")[:120],
                        "reason": getattr(self.router, "degraded_reason", "")[:200],
                    }, source=self.AGENT_ID)
            except Exception:
                pass

    # ── 统一任务生命周期(模板方法,P1:5 个 run_task 收敛为 1)──────
    # 领域 Agent 不再各自重写 run_task,只实现 _execute()(召回后的领域步骤)。
    # 会话开启、记忆召回、返回体结构、Harness 拦截处理都在此**唯一**定义,
    # 保证所有 Agent 生命周期一致(修复"每 Agent 各写一份、行为漂移")。

    _recall_topk: int = 5   # 召回条数(office 覆盖为 8)

    def _task_entities(self, task: str) -> list:
        """本任务的基线实体(用于 Harness 目标基线)。领域 Agent 覆盖。"""
        return []

    def _recall_query(self, task: str) -> str:
        """记忆召回查询(领域 Agent 可加前缀,如财务/行程)。默认用原任务。"""
        return task

    # ── 【P1-1】任务生命周期钩子 ────────────────────────────────
    # 崩溃/重启恢复记账(RestartRecovery)。会话开启后、管线执行前触发 started;任务终结时
    # 触发 settled(done=正常终态含 Harness 兜底/中断;failed=未兜底异常)。run_task 与
    # run_task_stream 共用 _run_task_core,故两条路径都会触发。
    # 实现放在基类(而不是只让主管覆盖):P1-1 时只有主管接了线,领域 Agent 跑一半崩掉
    # 在恢复扫描里根本不可见。没有 _recovery 的 standalone/测试 Agent → 直接跳过。
    def _on_session_started(self, session_id: str, task: str, context: dict) -> None:
        """会话已开、管线未跑:落 task_started(崩溃后才认得出"上次跑到一半")。"""
        rec = getattr(self, "_recovery", None)
        if rec is None:
            return
        try:
            rec.task_started(task_id=session_id, description=task,
                             session_id=session_id,
                             channel=(context or {}).get("channel", ""))
        except Exception:
            pass

    def _on_task_settled(self, session_id: str, status: str) -> None:
        """任务终结(done/failed):落 task_finished,把该 task 从"进行中"摘掉。"""
        rec = getattr(self, "_recovery", None)
        if rec is None:
            return
        try:
            rec.task_finished(session_id, status=status)
        except Exception:
            pass

    def _record_stage(self, session_id: str, stage: str) -> None:
        """管线 stage 级 checkpoint(fail-safe):断连/崩溃后能回答"跑到哪一步"。"""
        rec = getattr(self, "_recovery", None)
        if rec is None or not session_id:
            return
        try:
            rec.record_step(session_id, {"kind": "stage", "name": stage})
        except Exception:
            pass

    def _load_resume_cache(self, task_id: str) -> dict:
        """装载上次运行已完成的工具步骤 → {步骤指纹: {"name", "result"}}。

        只收「成功 + 未被截断」的步:失败步复用了等于把失败当完成,截断结果复用了等于
        喂给模型半份事实 —— 宁可不复用(重跑),也不给错的上下文。
        """
        rec = getattr(self, "_recovery", None)
        if rec is None or not task_id:
            return {}
        cache: dict = {}
        try:
            for st in rec.steps_of(task_id):
                if st.get("kind") != "tool" or not st.get("key") or st.get("truncated"):
                    continue
                res = st.get("result")
                if isinstance(res, str) and res.strip():
                    cache[st["key"]] = {"name": st.get("name", ""), "result": res}
        except Exception:
            return {}
        return cache

    def _cognitive_pipeline(self):
        """本 Agent 的认知管线(v2 ②)。默认 recall→act 两个 stage;领域 Agent 可覆盖以
        插入/替换 stage(后续步骤加 verify/reflect/plan/route/perceive)。缓存一份即可
        (stage 无状态,只操作传入的 ctx)。"""
        pipe = getattr(self, "_pipeline_cache", None)
        if pipe is None:
            from ..pipeline import CognitivePipeline, DEFAULT_STAGES
            pipe = self._pipeline_cache = CognitivePipeline(DEFAULT_STAGES)
        return pipe

    async def run_task(self, task: str, context: dict | None = None, stop_event=None) -> dict:
        """统一任务生命周期:开会话 → [认知管线:recall → act] → 组装返回。
        Harness BLOCK 统一在此兜底为结构化错误。返回
        {"result",...,"session_id","agent","thought_summary", **extra}。
        v2 ②:执行体由 CognitivePipeline 驱动(Step1 抽壳,行为与 P1-1 模板逐字一致)。

        stop_event(P3,opt-in,与 stream_reply 同一约定):threading/asyncio.Event,置位则让
        认知管线在下一 stage 边界 / reflect 重试前优雅停止;默认 None → 永不停,行为逐字不变。
        中断时返回体带 `"interrupted": True`,result 为已完成 stage 的部分产物。

        断点续跑(P1,opt-in):`context={"_resume_task_id": <上次 task_id>}` → 装载上次已完成
        的工具步骤,命中即复用结果不再重复执行;返回体带 `resumed_from` / `resumed_steps`
        说明续了哪一次、复用了多少步。"""
        return await self._run_task_core(task, context, stop_event=stop_event, on_stage=None)

    async def _run_task_core(self, task: str, context: dict | None = None,
                             stop_event=None, on_stage=None) -> dict:
        """run_task 的执行核心 —— 供同步 `run_task` 与流式 `run_task_stream` **共用**,避免两份
        生命周期逻辑漂移。`on_stage`(D1):逐 stage 进度回调,透传给管线;None=同步路径不回调,
        与改前 run_task 行为逐字一致。"""
        from ..pipeline import PipelineContext
        context = context or {}
        session_id = self.start_session(goal=task, entities=self._task_entities(task))
        # 来源可信性:渠道入口会带 untrusted_source=True(见 channels/dispatcher)。本任务里的
        # 写/发送/外发动作据此强制 HITL;本地 HTTP 对话不带此键 → 行为逐字不变。
        _untrusted_set(self, bool(context.get("untrusted_source")))
        # 【P1-1】生命周期钩子:会话已开、管线未跑。默认 no-op;主管借此做崩溃/重启
        # 恢复记账(task_started)——恢复记账需先于执行落日志,故必须在管线之前。
        self._on_session_started(session_id, task, context)
        # E2:pre_reasoning(agent_start)钩子——推理开始前触发,可注入 additionalContext(前插进 act
        # 的 LLM 上下文,见 _call_llm)。默认无钩子/无注入 → 行为逐字不变。hooks 由 Foxclaw 装配注入;
        # standalone/测试 Agent 无 hooks 属性 → getattr None → 整个钩子逻辑跳过。
        _hooks = getattr(self, "hooks", None)
        _inject_parts = []
        if _hooks is not None:
            _hr = _hooks.fire("agent_start", "", {"task": task, "agent_id": self.AGENT_ID})
            if _hr.additional_context:
                _inject_parts.append(_hr.additional_context)
        _pref = self._prefs_context()          # 偏好优先注入(独立于语义 query → 每次推理总是注入)
        if _pref:
            _inject_parts.append(_pref)
        # 偏好 + agent_start 钩子上下文合并进同一 _reasoning_inject → _call_llm 前插(复用 E2 机制)
        _inject_tok = _reasoning_inject.set("\n\n".join(_inject_parts)) if _inject_parts else None
        # 【P1 断点续跑】context={"_resume_task_id": <上次的 task_id/session_id>} 时,把上次
        # 已完成的工具步骤装进 contextvar(root_key→结果):工具轮命中同指纹步骤直接复用结果、
        # 不再重复执行。opt-in——不带该键 → 不装载、行为逐字不变。
        _resume_tid = str(context.get("_resume_task_id") or "").strip()
        _resume_cache = self._load_resume_cache(_resume_tid) if _resume_tid else {}
        _resume_tok = _resume_steps.set(_resume_cache) if _resume_cache else None
        ctx = PipelineContext(agent=self, task=task, context=context,
                              session_id=session_id, stop_event=stop_event)

        def _stage_hook(name, _ctx):
            """stage 边界:记一条 checkpoint(跑到哪一步),再转发给调用方的流式回调。"""
            self._record_stage(session_id, name)
            if on_stage is not None:
                on_stage(name, _ctx)

        try:
            await self._cognitive_pipeline().run(ctx, on_stage=_stage_hook)
            resp = {
                "result": ctx.result,
                "session_id": session_id,
                "agent": self.AGENT_ID,
                "thought_summary": self.thought_chain.get_summary(self.AGENT_ID),
                **(ctx.extra or {}),
            }
            _plan = _plan_payload(ctx)   # 计划可见:阻塞路径也回传,客户端无需再猜步骤
            if _plan:
                resp["plan"] = _plan
            if _resume_cache:        # 续跑透明化:告诉调用方"用了几个已完成步骤、从哪续的"
                resp["resumed_from"] = _resume_tid
                resp["resumed_steps"] = len(_resume_cache)
            if ctx.interrupted:      # P3:被 stop_event 提前中断 → 让调用方识别"部分完成"
                resp["interrupted"] = True
            # 【A·Step3·确认闸提升】act 若经 skill dispatch 且未成功(needs_confirmation/blocked/
            # circuit_open/error),把该态提升为 run_task **顶层 status**,让调用方无需深挖 result
            # 即可识别"待确认/未完成动作";needs_confirmation 附 pending_action(供确认后带
            # _confirmed 重跑恢复执行)。**未完成 ⇒ 不作为已完成真值沉淀**(blocked=True,呼应
            # "被拦截任务不沉淀记忆");成功/纯 LLM 路径行为逐字不变。
            skill_status = (ctx.extra or {}).get("skill_status")
            skill_incomplete = bool(skill_status) and skill_status != "done"
            if skill_incomplete:
                resp["status"] = skill_status
                if skill_status == "needs_confirmation":
                    resp["pending_action"] = {
                        "skill_id": (ctx.extra or {}).get("skill_id"),
                        "task": task,
                        "message": (ctx.result or {}).get("message", "")
                                   if isinstance(ctx.result, dict) else "",
                        "hint": "确认后带 context={'_confirmed': <skill_id>, ...} 重跑本任务以执行"
                                "(绑定到 skill_id 更安全;_confirmed=True 为概括确认)",
                    }
            # 「未完成 ⇒ 不作为已完成真值沉淀」的 blocked 判定,须同时覆盖两类未完成:
            #   ① skill 未成功(needs_confirmation/blocked/circuit_open/error);
            #   ② 被 stop_event **中断**(B3 深审 G2):流式任务客户端断连 → 中断,ctx.result 只是
            #      部分/空产物,绝不能当"已完成真值"沉淀进 HMR 共享记忆(呼应 P5/Principle07)。
            #      P3 引入 interrupted 时未联动此处;B3 的 /api/task/stream 是首个真正会 set stop_event
            #      的生产调用方,才使这条路径可达。非中断路径 blocked 值与改前逐字一致。
            blocked = skill_incomplete or ctx.interrupted
            self._emit_execution(task, ctx.result, blocked=blocked, context=context)
            self._maybe_store_preference(task)   # 用户偏好表达 → 持久记(preference 类型,下次总是注入)
            if _hooks is not None:   # E2:task_complete(agent_end)——任务完成后只读观测/上报(对应范式 Stop)
                _hooks.fire("agent_end", "", {"task": task, "agent_id": self.AGENT_ID,
                                              "result": resp.get("result"),
                                              "interrupted": bool(ctx.interrupted)})
            self._on_task_settled(session_id, "done")   # 【P1-1】正常终态(含中断/被拦提升)
            return resp
        except HarnessBlockedError as e:
            self._emit_execution(
                task, {"error": "harness_blocked", "reason": e.result.reason},
                blocked=True, context=context)
            if _hooks is not None:   # task_complete 也覆盖被拦截结束(带 error 供上报)
                _hooks.fire("agent_end", "", {"task": task, "agent_id": self.AGENT_ID,
                                              "error": "harness_blocked"})
            self._on_task_settled(session_id, "done")   # 被拦截是已兜底的正常终态(非崩溃)
            return {"error": "harness_blocked", "reason": e.result.reason,
                    "session_id": session_id}
        except Exception:
            self._on_task_settled(session_id, "failed")  # 【P1-1】未兜底异常 → failed(供恢复)
            raise
        finally:
            if _inject_tok is not None:
                _reasoning_inject.reset(_inject_tok)   # 复位注入 contextvar,不泄漏到后续任务
            if _resume_tok is not None:
                _resume_steps.reset(_resume_tok)       # 续跑缓存不泄漏到后续任务

    def run_task_stream(self, task: str, context: dict | None = None, stop_event=None):
        """流式任务执行(D1):**同步 generator**,逐 stage 吐进度 + act 逐 token 出字 + 最终结果。
        产出事件(dict,逐条给 SSE):
          {"stage": name}   每完成一个认知 stage(perceive→…→reflect)
          {"plan": {...}}   紧跟 plan stage:显式计划 {steps, flow, multi_step}(计划可见)
          {"delta": text}   act 的 LLM 逐 token(仅纯 LLM 路径;skill dispatch 路径无 token)
          {"result": {...}} 最终 run_task 返回体(含 interrupted/status/pending_action/plan 等)
          {"error": msg}    HarnessBlock 之外的异常(HarnessBlock 已被核心兜底进 result)

        与 `stream_reply` 同构(同步 generator,由 SSE 端点在后台线程迭代),复用共享的
        `_run_task_core`,故生命周期/返回体与非流式 `run_task` **完全一致**——只是多了逐 stage /
        逐 token 的即时反馈。`stop_event`(客户端断连)一处置位、两处生效:停管线 stage(P3)+
        停 act 的 LLM 生成(省 token)。opt-in:不走此方法的 `run_task` 路径完全不受影响。

        实现:worker 线程跑 `asyncio.run(_run_task_core(..., on_stage=…))`(与 /api/task 一样在
        工作线程内起独立事件循环),stage 回调 + token 回调把事件塞进线程安全 `queue.Queue`,
        本 generator 主体 drain 该队列并 yield。`_act_stream` contextvar 在 worker 内设置,随
        asyncio Task / to_thread 的上下文复制传播到 `_call_llm` → 触发 act 流式。"""
        import queue as _queue, threading as _threading, asyncio as _asyncio
        bridge: "_queue.Queue" = _queue.Queue()
        _SENTINEL = object()

        def _on_stage(name, _ctx):
            bridge.put({"stage": name})
            if name == "plan":               # 计划显式化:steps 随事件送给前端/调用方
                _plan = _plan_payload(_ctx)
                if _plan:
                    bridge.put({"plan": _plan})

        def _on_token(chunk):
            bridge.put({"delta": chunk})

        def _worker():
            token = _act_stream.set({"on_token": _on_token, "stop_event": stop_event})
            try:
                resp = _asyncio.run(self._run_task_core(
                    task, context, stop_event=stop_event, on_stage=_on_stage))
                bridge.put({"result": resp})
            except Exception as e:                 # 核心已兜底 HarnessBlock;此处兜其余意外
                bridge.put({"error": str(e)})
            finally:
                _act_stream.reset(token)
                bridge.put(_SENTINEL)              # 结束哨兵:保证 generator 必定收尾

        _threading.Thread(target=_worker, daemon=True, name="task-stream").start()
        while True:
            item = bridge.get()
            if item is _SENTINEL:
                break
            yield item

    def _emit_execution(self, task, result, blocked: bool, context: dict):
        """【P2·全路径触发】把本 Agent 的任务执行发到事件脊柱 → TruthPipeline(共享真值记录)
        + CognitiveState/WS 可观测。修复"共享真值记录只在主管分发路径产生"的不一致:
        现在直连领域 Agent 的任务也统一走这条真值链。

        被主管分发/路由调用时(context['_no_emit']=True)跳过——由主管任务级别的统一
        `_emit_execution` 承载(P1-1 后主管也走本模板),避免同一子任务双发。
        经能力中枢解析事件脊柱;无中枢(standalone/治理场景)则静默跳过。"""
        if context.get("_no_emit"):
            return
        caps = getattr(self, "_caps", None)
        spine = caps.get("event_spine") if caps is not None else None
        if spine is None:
            return
        try:
            spine.publish("execution.completed", {
                "agent_id": self.AGENT_ID, "task": task,
                "result": result, "blocked": blocked}, source=self.AGENT_ID)
        except Exception:
            pass

    async def _execute(self, task: str, context: dict, session_id: str,
                       memories: list) -> tuple:
        """领域执行步骤(召回之后 → 返回之前)。返回 (result, extra_dict)。
        领域 Agent 必须实现。SupervisorAgent 例外:它的 pipeline 不含 act stage
        (路由分发由 supervisor_stages 的 dispatch stage 承担),不会走到本钩子。"""
        raise NotImplementedError(f"{self.AGENT_ID} 未实现 _execute()")

    # ── 编排协作接口(P3:领域知识归属领域 Agent,编排层只做通用聚合/路由)──────
    def situation(self) -> dict:
        """本 Agent 的领域态势:声明自己的 domain + 状态 + 告警。默认无领域态势。
        领域 Agent 覆盖。编排层据此**通用聚合**,不需懂任何领域内部 API。
        返回 {} 或 {"domain": str, "status": dict, "alerts": [ {domain,severity,msg} ]}。"""
        return {}

    def concede(self, req, verdict: str):
        """【协商闭环】本 Agent 作为**发起方**被拒后,能否让步?

        返回修订后的 payload(dict)→ 编排层拿去再裁决一轮;返回 None → 让无可让,认输。
        默认 None:**不让步**,行为与协商前逐字一致 —— 所以给所有未实现本钩子的 Agent
        接上协商协议是零风险的(只有实现了 concede 的领域 Agent 才会真的多跑回合)。

        让步逻辑必须留在**发起方领域**(编排层不懂"改高铁/换酒店"是什么意思)。
        """
        return None

    def handle_cross_request(self, req) -> None:
        """处理跨 Agent 请求,用**本领域守门人**裁决(就地写 req.status / req.verdict)。
        默认:无特殊审批,放行。领域 Agent 覆盖。req 为鸭子类型(读 request_type/payload,
        写 status/verdict),故不与 orchestration 形成 import 依赖。"""
        req.status = "approved"
        req.verdict = "无需特殊审批"

    # ── 通用状态查询 ───────────────────────────────────────

    def get_status(self) -> dict:
        return {
            "agent_id": self.AGENT_ID,
            "agent_name": self.AGENT_NAME,
            "session_id": self._session_id,
            "harness": {
                "shadow_mode": self.harness.is_shadow_mode,
                "thresholds": self.harness.thresholds,
                "policy": self.harness.get_policy_summary(),
            },
            "thought_chain": self.thought_chain.get_summary(self.AGENT_ID),
        }
