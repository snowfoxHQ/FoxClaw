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

from ._context import _taint_get, _taint_raise, _untrusted_get
from ...core import settings
from ...core.injection_guard import redact_obj, redact_secrets
from typing import Any

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
            except BaseException as e:
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
    _errs = _reg.validate_args(name, args or {})
    if _errs:
        agent._think(content=f"工具 {name} 入参校验失败: {'; '.join(_errs)[:120]}",
                     node_type="tool_intent", conf=0.9)
        return (f"(工具 {name} 入参不合法: {'; '.join(_errs)}。"
                f"请按工具声明修正参数后重试。)"), {}
    cap = _tools.tool_capability(name)
    _schema = _reg.param_schema(name)
    _req = set(_schema.get("required") or [])
    _params = {k: {"required": k in _req, "type": v.get("type")}
               for k, v in (_schema.get("properties") or {}).items()
               if isinstance(v, dict)}
    spec = _types.SimpleNamespace(id=f"tool:{name}", params=_params, **cap)
    _taint = _taint_get(agent)
    if _untrusted_get(agent):
        _taint = max(_taint, _ig.TAINT_PERSONAL)
    decision = _perm.evaluate(spec, args or {}, confirmed=False,
                              taint_level=_taint,
                              taint_threshold=_ig.TAINT_PERSONAL)
    if decision.decision == "deny":
        return f"(工具 {name} 被权限策略拒绝:{decision.reason},未执行)", cap
    if decision.decision == "ask":
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
            if _ws.is_personal_query(q):
                return "(该问题属于用户本地数据,已跳过联网;请依据本地领域信息回答)"
            payload = _ws.search_answer(q)
            if not payload or (not payload.get("answer") and not payload.get("references")):
                return ("联网搜索未返回结果(可能未配置搜索服务或网络不可用);"
                        "请如实告知用户暂时无法查询实时信息,切勿编造版本号/新闻/价格等。")
            agent._think(content=f"工具 web_search「{q}」命中", node_type="tool_use", conf=0.9)
            _raise(_ig.taint_level("web_search"))
            return _ws.format_for_prompt(q, payload)
        if name == "recall_memory":
            q = str((args or {}).get("query", "")).strip()
            try:
                top_k = max(1, min(20, int((args or {}).get("top_k", 5))))
            except Exception:
                top_k = 5
            res = agent._recall_memory(q, top_k=top_k) if q else []
            if not res:
                return "(未召回到相关记忆)"
            lines = "\n".join(f"- {r.get('content', '')}" for r in res[:top_k])
            _raise(_ig.taint_level("memory"))
            return "【召回的相关记忆】\n" + _ig.wrap_untrusted(lines, source="memory")
    except Exception as e:
        return f"(工具 {name} 执行异常:{type(e).__name__})"
    return f"(未知工具 {name},未执行)"


def _timeout_text(name: str) -> str:
    return (f"(工具 {name} 超过 {_tool_timeout():.0f}s 未返回,已跳过本次调用。"
            f"请如实告知用户这一步没完成,不要编造结果。)")


def _merge_taint(agent, sink: list) -> None:
    for lvl in sink:
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
            ok, val = sp.wait(left)
        except Exception as e:
            out.append(f"(工具 {n} 执行异常:{type(e).__name__})")
            continue
        if not ok:
            out.append(_timeout_text(n))
            continue
        _merge_taint(agent, sink)
        out.append(val)
    return out


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
    """把一个已完成的工具步骤写进 recovery 日志(fail-safe:异常一律吞掉)。

    落盘前对 args/result 过 `redact_secrets`:recovery 日志会被回放读回,长期留在
    `{data_dir}/recovery/` 下,而工具参数里经常出现 token/密钥(如鉴权类 CLI、带 key 的
    HTTP 请求)。`_step_key` 仍用**未脱敏**的 args 计算,否则同一步骤的 key 前后不一致,
    缓存去重和回放对不上。
    """
    rec = getattr(agent, "_recovery", None)
    tid = getattr(agent, "_session_id", "") or ""
    if rec is None or not tid:
        return
    try:
        text = redact_secrets(result or "")
        rec.record_step(tid, {
            "kind": "tool",
            "name": call.get("name", ""),
            "key": _step_key(call.get("name", ""), call.get("args") or {}),
            "args": redact_obj(call.get("args") or {}),
            "result": text[:_STEP_RESULT_MAX],
            "truncated": len(result or "") > _STEP_RESULT_MAX,
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
        v = _verify.verify(name, args, result, cap)
        note = _verify.note_for_model(v, read_only=bool(cap.get("read_only")))
        if note:
            text = f"{text}\n{note}"
        _evidence_add(agent, {
            "tool": name, "kind": kind, "status": v.status,
            "reason": v.reason, "evidence": [e.to_dict() for e in v.evidence],
        })
        if v.status != _verify.FAILED and _step_ok(result):
            _record_step(agent, call, result)
    except Exception:
        pass
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
    AGENT_ID: str
    router: Any
    system_prompt: str
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
            _compact = getattr(self, "_compact_context", None)
            if _compact is not None:
                conv = _compact(conv, "tool_loop")
            if stop_event is not None and stop_event.is_set():
                return None
            res = self.router.call_tools(self.system_prompt, conv, schemas,
                                         agent_id=self.AGENT_ID)
            if not res.get("supported"):
                return None
            calls = res.get("tool_calls") or []
            if not calls:
                return res.get("text") or None
            conv.append(res["assistant_msg"])
            results = _run_tool_calls(self, calls)
            for c, result in zip(calls, results):
                conv.append(self.router.format_tool_result(c, result))
        final = self.router.call_tools(self.system_prompt, conv, schemas,
                                       tool_choice="none", agent_id=self.AGENT_ID)
        if not final.get("supported"):
            return None
        return final.get("text") or None
