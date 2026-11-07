"""ToolExecMixin:LLM 自主工具选择的分发执行 + 工具调用循环。

从 `foxclaw/agents/base.py` 搬移出来的工具执行相关方法。

实现要点:
- 闸门/执行拆成**模块级函数**(`_guard_tool(agent, ...)`),而不是自调用 `self.xxx`。
  原因:`_dispatch_tool` 一直被单测以 `CognitiveAgent._dispatch_tool(fake_agent, ...)` 的
  方式用 SimpleNamespace 当 self 调用(鸭子类型),自调用新方法会 AttributeError。
- 每个工具调用跑在守护线程里,带单工具硬超时;只读且声明 parallel_safe 的工具同轮并发。
- 会话污点在 worker 线程里只收集、回主线程合并(contextvar 按线程隔离,直接 set 会丢)。
"""
import threading
import time

from ._context import _taint_get, _taint_raise
from ...core import settings

#: 同轮最多并发几个工具(再多也是网络等待,收益递减;避免打爆 provider/搜索服务)
MAX_PARALLEL = 4


def _tool_timeout() -> float:
    """单工具硬超时(秒),FOXCLAW_TOOL_TIMEOUT 可调;默认 45s。

    值写错不拖垮工具执行(退默认),但会 WARN 一次指出是哪个变量错了 ——
    此前是 try/except 静默退默认,用户的设置有错也永远不知道。
    """
    v = float(settings.get_safe("FOXCLAW_TOOL_TIMEOUT"))
    return v if v > 0 else 45.0


class _Spawn:
    """把一次工具调用扔进守护线程:超时后工具轮照常继续,不被一个慢工具整轮拖死。

    用 daemon 线程而非 ThreadPoolExecutor:被放弃的慢调用不会在解释器退出时阻塞进程
    (线程杀不掉,但至少不挡收尾)。
    """

    __slots__ = ("done", "box", "thread")

    def __init__(self, fn):
        self.done = threading.Event()
        self.box: dict = {}

        def _run():
            try:
                self.box["v"] = fn()
            except BaseException as e:      # 统一交主线程降级成错误文本
                self.box["e"] = e
            finally:
                self.done.set()

        self.thread = threading.Thread(target=_run, daemon=True, name="sf-tool")
        self.thread.start()

    def wait(self, timeout: float):
        """返回 (是否按时完成, 结果);超时 (False, None),异常在主线程重抛。"""
        if not self.done.wait(timeout):
            return False, None
        if "e" in self.box:
            raise self.box["e"]
        return True, self.box.get("v")


def _guard_tool(agent, name: str, args: dict):
    """跑权限管线,返回 (提前结束文本 | None, 能力标签 dict)。

    动作级安全阀(真正的兜底,不指望 prompt 隔离挡住弱模型):
      ① per-tool deny 闸(kill-switch);
      ② 未知工具 → 不执行;
      ③ **四阶段权限管线**(permission.evaluate):按能力分级裁决 allow/ask/deny——
         只读放行;写/发送/不可逆/外部副作用/未声明的新工具 → **人在回路(HITL)**,只出草稿不自动执行;
      ④ web_search 的个人数据否决留到执行阶段(需要 query)。
    纯函数式、无副作用,因此并发调度前可在主线程一次性裁决完。
    """
    import types as _types
    from ...core import tools as _tools
    from ...core import injection_guard as _ig
    from ...skills import permission as _perm
    if _tools.is_tool_denied(name):
        return f"(工具 {name} 已被策略禁用,未执行)", {}
    _reg = _tools.default_registry()
    if not _reg.has(name):
        return f"(未知工具 {name},未执行)", {}
    # ── 参数校验(用注册表里同一份 JSON Schema)────────────────────
    # 修复前:schema 只发给模型,执行侧不校验 → 模型给错类型/漏参数会直接流到
    # 下游。且下方 permission Stage 1 因 params={} 恒不触发,只靠手工 if-not 兜。
    # 新增校验闸后,错误**回注模型**让其自修正,而不是静默用空参数继续
    # (那会让模型误以为工具已执行,进而编造基于"工具结果"的结论)。
    _errs = _reg.validate_args(name, args or {})
    if _errs:
        agent._think(content=f"工具 {name} 入参校验失败: {'; '.join(_errs)[:120]}",
                     node_type="tool_intent", conf=0.9)
        return (f"(工具 {name} 入参不合法: {'; '.join(_errs)}。"
                f"请按工具声明修正参数后重试。)"), {}
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
                              taint_level=_taint_get(agent),
                              taint_threshold=_ig.TAINT_PERSONAL)
    if decision.decision == "deny":
        return f"(工具 {name} 被权限策略拒绝:{decision.reason},未执行)", cap
    if decision.decision == "ask":
        # 红线/副作用/未声明工具 → 人在回路:不自动执行,交回由用户确认(KERNEL §6 同旨)
        agent._think(content=f"工具 {name} 需用户确认({decision.reason}),已暂停执行",
                     node_type="decision", conf=0.9)
        return (f"(工具 {name} 属于需确认的动作:{decision.reason}。按安全策略我不会自动执行——"
                f"这类涉及发送/转账/删除/修改/对外调用的红线动作只能由你亲自确认后进行;"
                f"我可以先把要做的操作拟成草稿供你审。"), cap
    return None, cap


def _invoke_tool(agent, name: str, args: dict, taint_sink: list | None = None) -> str:
    """执行工具本体,返回回注模型的文本。

    taint_sink 非空 = 当前在 worker 线程:会话污点在 contextvar 里按线程隔离,
    直接 `_taint_raise` 会写进线程自己的副本然后丢失 → 先收集,交调用方在主线程合并。
    """
    from ...core import injection_guard as _ig

    def _raise(level: int) -> None:
        if taint_sink is not None:
            taint_sink.append(level)
        else:
            _taint_raise(agent, level)

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
            agent._think(content=f"工具 web_search「{q}」命中", node_type="tool_use", conf=0.9)
            _raise(_ig.taint_level("web_search"))   # 外部不可控数据 → 会话污点
            return _ws.format_for_prompt(q, payload)
        if name == "recall_memory":
            q = str((args or {}).get("query", "")).strip()
            # top_k 此前硬编码 5,模型传值无效 → 加边界校验后尊重模型意图
            # (schema 已声明 top_k,1-20;越界回落默认,防一次召回拉爆上下文)
            try:
                top_k = max(1, min(20, int((args or {}).get("top_k", 5))))
            except Exception:
                top_k = 5
            res = agent._recall_memory(q, top_k=top_k) if q else []
            if not res:
                return "(未召回到相关记忆)"
            lines = "\n".join(f"- {r.get('content', '')}" for r in res[:top_k])
            # 记忆可能被投毒(此前注入固化) → 当作不可信数据回注,防记忆投毒二次生效
            _raise(_ig.taint_level("memory"))   # 个人记忆 → 会话污点(高敏感)
            return "【召回的相关记忆】\n" + _ig.wrap_untrusted(lines, source="memory")
    except Exception as e:
        return f"(工具 {name} 执行异常:{type(e).__name__})"
    return f"(未知工具 {name},未执行)"


def _timeout_text(name: str) -> str:
    return (f"(工具 {name} 超过 {_tool_timeout():.0f}s 未返回,已跳过本次调用。"
            f"请如实告知用户这一步没完成,不要编造结果。)")


def _merge_taint(agent, sink: list) -> None:
    for lvl in sink:                      # 只在主线程动会话污点(单调取 max)
        try:
            _taint_raise(agent, lvl)
        except Exception:
            pass


def _exec_guarded(agent, name: str, args: dict) -> str:
    """已过闸门的单个工具:带超时执行 + 主线程合并污点。"""
    sink: list = []
    sp = _Spawn(lambda: _invoke_tool(agent, name, args, sink))
    try:
        ok, out = sp.wait(_tool_timeout())
    except Exception as e:
        return f"(工具 {name} 执行异常:{type(e).__name__})"
    if not ok:
        return _timeout_text(name)
    _merge_taint(agent, sink)
    return out


def _run_tools_batch(agent, calls: list) -> list:
    """按能力调度一批 tool_calls,返回与 calls **同序**的结果文本。

    只读 + 显式声明 parallel_safe + 全部过闸 → 并发(典型:web_search + recall_memory
    同轮发出,不再一个等一个);其余(写/发送/需确认/未声明)一律串行,避免同一批里
    副作用动作互相覆盖 HITL 裁决。
    """
    plans = []
    for c in calls:
        n = c.get("name", "")
        a = c.get("args") or {}
        early, cap = _guard_tool(agent, n, a)
        plans.append((n, a, early, cap))
    parallel = (len(plans) > 1 and len(plans) <= MAX_PARALLEL
                and all(p[2] is None and p[3].get("parallel_safe") for p in plans))
    if not parallel:
        out = []
        for p in plans:
            if p[2] is not None:
                out.append(p[2])
                continue
            # 串行走 agent._dispatch_tool(保留「实例级覆盖」这一扩展点,单测常这么替身);
            # 没有该方法时才直接执行。
            _disp = getattr(agent, "_dispatch_tool", None)
            out.append(_disp(p[0], p[1]) if callable(_disp)
                       else _exec_guarded(agent, p[0], p[1]))
        return out
    spawns = []
    for n, a, _early, _cap in plans:
        sink: list = []
        spawns.append((n, _Spawn(lambda n=n, a=a, s=sink: _invoke_tool(agent, n, a, s)), sink))
    started = time.monotonic()
    timeout = _tool_timeout()
    out = []
    for n, sp, sink in spawns:
        left = max(0.0, timeout - (time.monotonic() - started))
        try:
            ok, val = sp.wait(left)        # 各自独立计时:一个超时不拖累已完成的
        except Exception as e:
            out.append(f"(工具 {n} 执行异常:{type(e).__name__})")
            continue
        if not ok:
            out.append(_timeout_text(n))
            continue
        _merge_taint(agent, sink)
        out.append(val)
    return out


#: checkpoint 里单步结果最多存多少字符(超出即标记 truncated,续跑时不复用、改为重跑)
_STEP_RESULT_MAX = 4000


def _step_key(name: str, args: dict) -> str:
    """步骤指纹:同名 + 同参(与字典序无关)= 同一步。续跑靠它匹配「这步之前做过」。"""
    import hashlib
    import json
    try:
        payload = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        payload = repr(args)
    return f"{name}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def _step_ok(result: str) -> bool:
    """这一步是否值得记进 checkpoint(只有「真的做成了」的步才可复用)。

    工具失败/超时/需确认/未执行的回注文本全部以 "(" 开头(见 _guard_tool/_timeout_text/
    _invoke_tool 的兜底),据此判定;复用它们等于把一次失败当成已完成,绝不能干。
    """
    r = (result or "").strip()
    return bool(r) and not r.startswith("(")


def _record_step(agent, call: dict, result: str) -> None:
    """把一个已完成的工具步骤写进 recovery 日志(fail-safe:异常一律吞掉)。"""
    rec = getattr(agent, "_recovery", None)
    tid = getattr(agent, "_session_id", "") or ""
    if rec is None or not tid:
        return
    try:
        text = result or ""
        rec.record_step(tid, {
            "kind": "tool",
            "name": call.get("name", ""),
            "key": _step_key(call.get("name", ""), call.get("args") or {}),
            "args": call.get("args") or {},
            "result": text[:_STEP_RESULT_MAX],
            "truncated": len(text) > _STEP_RESULT_MAX,
        })
    except Exception:
        pass


def _apply_failure_policy(agent, call: dict, result: str) -> tuple[str, str]:
    """按策略表决定「这次失败怎么处理」,返回 (回注文本, 失败类型)。

    · 只读工具遇到超时/网络/异常 → **自动重试一次**(读操作重复执行无副作用);
    · 写/发送/支付类一律不重试(重试 = 可能重复扣款/重复发消息),交人;
    · 其余(未配置/参数错/红线)只注入动作提示,不重试。
    重试只是把「要不要再来一次」从模型手里拿走,并不隐瞒失败:两次都失败时文本里
    仍写明未完成。
    """
    from ...core import failure_policy as _policy
    from ...core import tools as _tools
    name = call.get("name", "")
    cap = _tools.tool_capability(name)
    d = _policy.decide(result, tool=name, capability=cap, attempt=1)
    if d.action == _policy.RETRY:
        try:
            retry = _run_tools_batch(agent, [call])[0]
        except Exception:
            retry = result
        d2 = _policy.decide(retry, tool=name, capability=cap, attempt=2)
        return _policy.annotate(retry, d2), d2.kind
    return _policy.annotate(result, d), d.kind


def _settle(agent, call: dict, result: str) -> str:
    """一个工具步的收尾:核验 → 记账证据 → 只把「真的可用」的步写进 checkpoint。

    返回回注给模型的最终文本(失败时附动作提示,成功时原样返回)。
    """
    from ...core import tools as _tools
    from ...core import verify as _verify
    from ._context import _evidence_add
    name = call.get("name", "")
    args = call.get("args") or {}
    text, kind = _apply_failure_policy(agent, call, result)
    try:
        cap = _tools.tool_capability(name)
        # 核验看的是**工具原始返回**,不是我们追加过提示的文本 —— 否则提示里的
        # "超时/未完成"字样会反过来影响判定。
        v = _verify.verify(name, args, result, cap)
        note = _verify.note_for_model(v, read_only=bool(cap.get("read_only")))
        if note:
            text = f"{text}\n{note}"
        _evidence_add(agent, {
            "tool": name, "kind": kind, "status": v.status,
            "reason": v.reason, "evidence": [e.to_dict() for e in v.evidence],
        })
        # 判定"可复用"以核验结论为准,而不是文本开头是不是 "(" ——
        # 失败文本追加提示后就不再以 "(" 开头,靠形态判会把它当成功存进断点。
        if v.status != _verify.FAILED and _step_ok(result):
            _record_step(agent, call, result)
    except Exception:
        pass                              # 核验/记账失败不影响工具结果本身
    return text


def _run_tool_calls(agent, calls: list) -> list:
    """执行一批 tool_calls,并按断点续跑语义处理(返回与 calls 同序的结果文本):
      · 命中 `_resume_steps` 里的同指纹步骤 → 复用上次结果,不再执行(断连/崩溃后重跑不重复);
      · 其余照常执行(只读并发 / 写串行,单工具带超时);
      · 每个结果过一遍失败策略表(只读可重试一次)+ 后置核验,证据挂到会话槽。
    """
    from ._context import _resume_steps
    cache = _resume_steps.get() or {}
    out: list = [None] * len(calls)
    todo: list = []
    for i, c in enumerate(calls):
        hit = cache.get(_step_key(c.get("name", ""), c.get("args") or {}))
        if isinstance(hit, dict) and isinstance(hit.get("result"), str):
            out[i] = (f"(断点续跑:{c.get('name', '')} 这一步此前已完成,"
                      f"以下为上次的结果,不必重做)\n{hit['result']}")
        else:
            todo.append(i)
    if todo:
        results = _run_tools_batch(agent, [calls[i] for i in todo])
        for i, r in zip(todo, results):
            out[i] = _settle(agent, calls[i], r)
    return out


class ToolExecMixin:
    # 以下四个方法均为薄包装:真实 agent 走 self.xxx;单测可用 SimpleNamespace 直接调
    # 模块级函数或 `CognitiveAgent._dispatch_tool(fake, ...)`(鸭子类型)。
    def _guard_tool(self, name: str, args: dict):
        return _guard_tool(self, name, args)

    def _invoke_tool(self, name: str, args: dict, taint_sink: list | None = None) -> str:
        return _invoke_tool(self, name, args, taint_sink)

    def _exec_guarded(self, name: str, args: dict) -> str:
        return _exec_guarded(self, name, args)

    def _dispatch_tool(self, name: str, args: dict) -> str:
        """执行一个被 LLM 选中的工具,返回结果文本(注回模型)。"""
        early, _cap = _guard_tool(self, name, args)
        if early is not None:
            return early
        return _exec_guarded(self, name, args)

    def _run_tools(self, calls: list) -> list:
        return _run_tools_batch(self, calls)

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
            # 上下文预算:工具轮每轮追加 assistant.tool_calls + tool 结果,不压会撑爆窗口
            # (Compactor 保证 tool_calls 与其结果同进同出,缺口见 core/compaction.py:100)。
            # getattr 兜底:本 mixin 也被只实现 router 的轻量对象复用(_compact_context
            # 定义在 CognitiveAgent 上),缺该方法时退化为不压缩。
            _compact = getattr(self, "_compact_context", None)
            if _compact is not None:
                conv = _compact(conv, "tool_loop")
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
            # 只读并发 / 写串行 / 单工具带超时 + 每步 checkpoint + 续跑复用已完成步骤
            results = _run_tool_calls(self, calls)
            for c, result in zip(calls, results):
                conv.append(self.router.format_tool_result(c, result))
        # 轮数超限 → 强制不再调工具、收敛出文本答案
        final = self.router.call_tools(self.system_prompt, conv, schemas,
                                       tool_choice="none", agent_id=self.AGENT_ID)
        if not final.get("supported"):
            return None                          # 收敛调用失败 → fallback(不再空回复不回退)
        return final.get("text") or None         # 收敛出空答 → None → fallback
