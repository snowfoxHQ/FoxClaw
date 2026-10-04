"""
Hestia OS — 多模型路由(Model Router)
体现 Hestia 的核心架构哲学:认知层与 LLM 解耦,大模型只是可替换的文本生成器。
所有 Agent 通过 Router 调模型,换模型不影响 ThoughtChain / Harness / 领域内核。

支持的 provider:
  - anthropic   Claude       (ANTHROPIC_API_KEY)
  - openai      GPT          (OPENAI_API_KEY)
  - deepseek    DeepSeek     (DEEPSEEK_API_KEY,OpenAI 兼容接口;界面推荐)
  - google      Gemini       (GOOGLE_API_KEY)
  - qwen        通义千问      (DASHSCOPE_API_KEY,OpenAI 兼容接口)
  - local       本地模型      (LOCAL_LLM_BASE,如 Ollama / vLLM,OpenAI 兼容)
  - stub        无 key 降级    (始终可用,保证系统可独立运行)

每个 provider 用各自 SDK,但对外暴露统一的 complete(system, messages) 接口。
"""

import os
import threading
import re as _re
from typing import Optional


# 各 provider 的默认模型(可被环境变量覆盖)
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai":    "gpt-4o",
    "deepseek":  "deepseek-chat",
    "google":    "gemini-1.5-pro",
    "qwen":      "qwen-plus",
    "local":     "llama3",
}

# 分层模型:fast(便宜快,分诊/简单)/ smart(强,复杂/分析)。未列的 provider fast=smart=默认。
TIER_MODELS = {
    "anthropic": {"fast": "claude-haiku-4-20250514", "smart": DEFAULT_MODELS["anthropic"]},
    "deepseek":  {"fast": "deepseek-chat",           "smart": "deepseek-reasoner"},
    "openai":    {"fast": "gpt-4o-mini",             "smart": "gpt-4o"},
    "qwen":      {"fast": "qwen-turbo",              "smart": "qwen-plus"},
}

# 任务复杂度触发 smart 档的词
_SMART_HINTS = _re.compile(
    r"分析|规划|报告|方案|评估|深度|论证|拆解|设计|策略|analyz|plan|report|design|strateg|reason")


def _route_tier(agent_id: str, messages: list) -> str:
    """任务→模型档:supervisor 分诊→fast;含复杂词或长任务→smart;否则 fast。"""
    default = os.environ.get("HESTIA_ROUTE_DEFAULT_TIER", "smart").strip().lower()
    if agent_id == "supervisor":
        return "fast"
    text = ""
    for m in reversed(messages or []):
        if m.get("role") == "user":
            text = str(m.get("content") or ""); break
    if _SMART_HINTS.search(text) or len(text) > 200:
        return "smart"
    return "fast" if text else (default if default in ("fast", "smart") else "smart")


def _tier_model(provider: str, tier: str) -> str:
    t = TIER_MODELS.get(provider)
    if t and t.get(tier):
        return t[tier]
    return DEFAULT_MODELS.get(provider, "")

# provider → 所需环境变量
PROVIDER_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "deepseek":  "DEEPSEEK_API_KEY",
    "google":    "GOOGLE_API_KEY",
    "qwen":      "DASHSCOPE_API_KEY",
    "local":     "LOCAL_LLM_BASE",   # 本地用 base_url 而非 key
}


def _chunk_text(text, size: int = 8):
    """把文本切成小块逐个 yield(stub/降级时模拟流式,按字符定长,兼容中英)。"""
    text = str(text or "")
    for i in range(0, len(text), size):
        yield text[i:i + size]


# ── 重试退避(M6)────────────────────────────────────────────────────
# 病根:此前 fallback 链只有「换 provider」,无退避、无抖动 —— 循环内无任何 sleep,
# 冷却固定 20s。多实例同时宕机时会同步解冻(惊群)并紧密重试,加剧上游压力。
# 下面用指数退避 + **全抖动**(full jitter,sleep=random(0,min(cap,base*2^n))):
# 上限保证不会无限等待,抖动保证多实例不会同步重试。
import random as _rnd   # noqa: E402  (供下方 _backoff_delay 与冷却抖动使用)

# 冷却基准秒(乘抖动系数后使用)。可经 HESTIA_LLM_COOLDOWN 覆盖,线上调参不必改代码。
_COOLDOWN_BASE = float(os.environ.get("HESTIA_LLM_COOLDOWN", "20"))


def _backoff_delay(attempt: int, base: float = 0.5, cap: float = 8.0) -> float:
    """第 attempt 次失败后、换下一个 provider 前的等待秒数:指数退避 + 全抖动。

    attempt 从 0 起计。返回 [0, min(cap, base*2^attempt)] 上的均匀随机值。
    """
    return _rnd.uniform(0.0, min(cap, base * (2 ** max(0, attempt))))


class ModelRouter:
    """统一模型路由。Agent 只认 router.complete(),不关心背后是哪家模型。"""

    def __init__(self, provider: str = None, model: str = None):
        # 默认 provider:按环境变量自动探测第一个可用的
        self.provider = provider or self._auto_detect()
        # HESTIA_MODEL 可能在 .env 里存在但为空字符串,空值时回退默认模型
        _env_model = os.environ.get("HESTIA_MODEL", "").strip()
        self._locked_model = model or _env_model or ""   # 显式锁定 → 不分档(向后兼容)
        self.model = self._locked_model or DEFAULT_MODELS.get(self.provider, "")
        self._client = None
        self._clients = {}                               # provider -> client(fallback 懒建缓存)
        self._client_lock = threading.Lock()
        self._cooldown = {}                              # provider -> 冷却截止 monotonic(断路器:跳过刚宕机的家)
        self.fallback_reason = ""
        self.meter = None        # 可选 TokenMeter,由 HestiaOS 注入
        # ── 降级状态(A 类修复)────────────────────────────────────────
        # 区分两种「拿不到可用回复」,二者对外文案与副作用不同:
        #   · never_configured —— 从未配置任何模型 key(离线演示/自测),stub 是合理兜底;
        #   · call_failed      —— 配了 key 但调用失败/返回空串 → **故障**,禁止伪装成功。
        # 后者由 _mark_degraded 标记,上游据此**禁止写入长期记忆**(故障文本会污染召回),
        # 并在文案中显式声明未完成(与 base.py §二「真相优先」对齐)。
        self.degraded: bool = False
        self.degraded_reason: str = ""
        # per-call 降级标记(单次请求生命周期内消费,不跨调用泄漏)
        self._call_degraded: bool = False
        self._init_client()

    @staticmethod
    def _auto_detect() -> str:
        """选择 provider:优先 HESTIA_PROVIDER 显式指定,否则按优先级探测已配 key 的。"""
        # 1. 显式指定(HESTIA_PROVIDER=deepseek 即可强制用 DeepSeek)
        forced = os.environ.get("HESTIA_PROVIDER", "").strip().lower()
        if forced and forced in PROVIDER_ENV:
            return forced
        # 2. 自动探测:按优先级找第一个配了 key 的
        for p in ["anthropic", "openai", "deepseek", "google", "qwen", "local"]:
            if os.environ.get(PROVIDER_ENV[p]):
                return p
        return "stub"

    @property
    def active(self) -> str:
        return self.provider

    @property
    def is_stub(self) -> bool:
        return self.provider == "stub" or self._client is None

    def _build_client(self, p: str, model: str = None):
        """构造某 provider 的客户端(供 active 初始化 + fallback 懒建复用)。
        返回 (client_or_None, reason)。SDK 缺/无 key → (None, 原因)。"""
        try:
            if p == "anthropic":
                import anthropic
                key = os.environ.get("ANTHROPIC_API_KEY")
                return (anthropic.Anthropic(api_key=key, timeout=30.0) if key else None,
                        "" if key else "未配置 ANTHROPIC_API_KEY")
            elif p in ("openai", "deepseek", "qwen", "local"):
                try:
                    from openai import OpenAI
                except ImportError:
                    return None, "需安装 openai 库(pip install openai)"
                if p == "openai":
                    key = os.environ.get("OPENAI_API_KEY"); base = None
                elif p == "deepseek":
                    key = os.environ.get("DEEPSEEK_API_KEY"); base = "https://api.deepseek.com/v1"
                elif p == "qwen":
                    key = os.environ.get("DASHSCOPE_API_KEY")
                    base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
                else:
                    key = os.environ.get("LOCAL_LLM_KEY", "local")
                    base = os.environ.get("LOCAL_LLM_BASE", "http://localhost:11434/v1")
                return (OpenAI(api_key=key, base_url=base, timeout=30.0, max_retries=2)
                        if (key or base) else None,
                        "" if (key or base) else f"未配置 {p} 的 Key")
            elif p == "google":
                import google.generativeai as genai
                key = os.environ.get("GOOGLE_API_KEY")
                if key:
                    genai.configure(api_key=key)
                    return genai.GenerativeModel(model or self.model or _tier_model(p, "smart")), ""
                return None, "未配置 GOOGLE_API_KEY"
            return None, f"未知 provider: {p}"
        except Exception as e:
            return None, f"初始化失败: {e}"

    def _init_client(self):
        """初始化 active provider 的客户端(降级 stub)。"""
        self._client, self.fallback_reason = self._build_client(self.provider)
        with self._client_lock:
            self._clients[self.provider] = self._client

    def _get_client(self, p: str):
        """取某 provider 客户端(缓存;fallback 时懒建)。"""
        if p == self.provider:
            return self._client
        with self._client_lock:
            if p in self._clients:
                return self._clients[p]
            c, _ = self._build_client(p)
            self._clients[p] = c
            return c

    def _fallback_order(self) -> list:
        """调用顺序:active 优先,再按优先级补上其它已配 key 的 provider。"""
        order = [self.provider]
        for p in ["anthropic", "openai", "deepseek", "google", "qwen", "local"]:
            if p != self.provider and os.environ.get(PROVIDER_ENV.get(p, ""), ""):
                order.append(p)
        return order

    def _model_for(self, provider: str, agent_id: str, messages: list) -> str:
        """按任务/Agent 选该 provider 的模型:锁定则用锁定值;否则按档。active provider 支持
        HESTIA_MODEL_FAST/SMART env 覆盖。"""
        if self._locked_model and provider == self.provider:
            return self._locked_model
        tier = _route_tier(agent_id, messages)
        if provider == self.provider:
            env = os.environ.get("HESTIA_MODEL_FAST" if tier == "fast" else "HESTIA_MODEL_SMART", "").strip()
            if env:
                return env
        return _tier_model(provider, tier)

    @staticmethod
    def _attach_images(provider, messages, images):
        """把图片附到最后一条 user 消息,按 provider 组装多模态 content。
        anthropic → image/base64 block;openai 系 → image_url(data URI)。
        其它 provider(google/未知)不支持 → 原样返回(纯文本降级)。
        messages 的文本仍供 tier 路由/stub/meter 用,故只在 provider 调用点临时组装,不改原列表。"""
        if not images:
            return messages
        msgs = [dict(m) for m in messages]
        idx = next((i for i in range(len(msgs) - 1, -1, -1)
                    if msgs[i].get("role") == "user"), None)
        if idx is None:
            return messages
        text = msgs[idx].get("content")
        if not isinstance(text, str):
            return messages                       # 已是多模态,不重复组装
        if provider == "anthropic":
            blocks = [{"type": "text", "text": text}]
            for im in images:
                blocks.append({"type": "image", "source": {
                    "type": "base64",
                    "media_type": im.get("media_type", "image/png"),
                    "data": im.get("data", "")}})
        elif provider in ("openai", "deepseek", "qwen", "local"):
            blocks = [{"type": "text", "text": text}]
            for im in images:
                mt = im.get("media_type", "image/png"); d = im.get("data", "")
                blocks.append({"type": "image_url",
                               "image_url": {"url": f"data:{mt};base64,{d}"}})
        else:
            return messages                       # google/未知:无视觉通道 → 纯文本
        msgs[idx]["content"] = blocks
        return msgs

    def _call_provider(self, provider, client, model, system, messages, max_tokens, images=None):
        """同步调一个 provider,返回 (output, usage);失败抛异常(供 fallback)。"""
        if images:
            messages = self._attach_images(provider, messages, images)
        if provider == "anthropic":
            resp = client.messages.create(model=model, max_tokens=max_tokens,
                                          system=system, messages=messages)
            u = getattr(resp, "usage", None)
            usage = ({"input_tokens": getattr(u, "input_tokens", None),
                      "output_tokens": getattr(u, "output_tokens", None)} if u else None)
            return resp.content[0].text, usage
        elif provider in ("openai", "deepseek", "qwen", "local"):
            msgs = [{"role": "system", "content": system}] + messages
            resp = client.chat.completions.create(model=model, max_tokens=max_tokens, messages=msgs)
            u = getattr(resp, "usage", None)
            usage = ({"input_tokens": getattr(u, "prompt_tokens", None),
                      "output_tokens": getattr(u, "completion_tokens", None)} if u else None)
            return resp.choices[0].message.content, usage
        elif provider == "google":
            prompt = system + "\n\n" + "\n".join(m["content"] for m in messages)
            return client.generate_content(prompt).text, None
        raise RuntimeError(f"unknown provider {provider}")

    # ── 统一补全接口 ──────────────────────────────────────
    @staticmethod
    def _weekday_cn(dt) -> str:
        return "一二三四五六日"[dt.weekday()]

    def _runtime_context(self) -> str:
        """运行时事实基准 —— 规则化注入到每次 LLM 调用的 system 头部。

        病根:过去"今天几号 / 你是什么模型"靠散落的关键词表(_model_augment / needs_search)
        临时猜,哪坏改哪、彼此打架。规则化解法:在 LLM **唯一咽喉**(complete/stream)统一提供
        一段权威运行时事实,所有 Agent、每次调用自动带上,不再靠关键词触发。
        模型无关:provider/model 取自当前路由配置,换任何模型都自动跟随,不硬编码。"""
        import datetime as _dt
        now = _dt.datetime.now()
        stamp = f"{now:%Y-%m-%d} 周{self._weekday_cn(now)} {now:%H:%M}"
        model = self.model or DEFAULT_MODELS.get(self.provider, self.provider)
        return (
            "【运行时信息(系统实时提供,为权威事实,高于对话历史中任何过时或矛盾的说法)】\n"
            f"- 当前日期时间:{stamp}(用户本地时区)。回答\"今天/现在/日期/几号/星期几\"一律以此为准。\n"
            f"- 你当前实际由 {self.provider} 的「{model}」模型驱动。被问及自身身份/型号时如实回答这个,"
            "绝不要声称是 GPT / Claude / 文心一言 / 云雀 等其它模型。\n\n"
        )

    def _inject_runtime(self, system: str) -> str:
        """把运行时事实基准前置到 system;幂等(已注入则不重复,防 stream→complete 二次注入)。"""
        s = system or ""
        if s.lstrip().startswith("【运行时信息"):
            return system
        return self._runtime_context() + s

    def complete(self, system: str, messages: list, max_tokens: int = 2048,
                 agent_id: str = "", images: list = None) -> str:
        """统一文本补全。messages 为 [{role, content}]。images(可选)= [{media_type,data}] 多模态图片。

        所有 LLM 调用的唯一咽喉——token 计量在此进行(若挂了 meter)。
        优先用 API 返回的真实 usage,拿不到则由 meter 离线估算。
        """
        system = self._inject_runtime(system)   # 规则化:运行时事实基准统一在咽喉注入
        if self.is_stub:
            # stub 不消耗 token,但计入 LLM 调用量(可观测);链路里也要看得见"这次走了 stub"
            try:
                from .metrics import METRICS
                METRICS.record_llm("stub", True, 0.0)
            except Exception:
                pass
            from .tracing import span as _span
            with _span("llm.complete", provider="stub"):
                return self._stub(messages)
        import time as _t
        last_err = None
        _now = _t.monotonic()
        for i, provider in enumerate(self._fallback_order()):
            if self._cooldown.get(provider, 0) > _now:     # 断路器:跳过刚宕机的家,免每次等超时
                continue
            client = self._get_client(provider)
            if client is None:
                continue
            model = self._model_for(provider, agent_id, messages)   # 按任务/Agent 选档
            _t0 = _t.perf_counter()
            try:
                from .tracing import span as _span
                with _span("llm.complete", provider=provider, model=model):
                    if images:   # 有图 → 传第 7 参数;无图保持 6 参数(兼容 test 里 6 参 monkeypatch)
                        output, usage = self._call_provider(provider, client, model,
                                                            system, messages, max_tokens, images)
                    else:
                        output, usage = self._call_provider(provider, client, model,
                                                            system, messages, max_tokens)
            except Exception as e:
                self._rec_llm(provider, False, (_t.perf_counter() - _t0) * 1000.0)
                # 冷却时长加抖动(D 类修复):原为固定 20.0,多实例同时宕机会**同步解冻**
                # (惊群)。抖动后各实例解冻时间分散,上游有喘息窗口。
                self._cooldown[provider] = _t.monotonic() + (
                    _COOLDOWN_BASE * (0.75 + 0.5 * _rnd.random()))
                last_err = e
                # 退避 + 全抖动(M6):换 provider 前让出时间,避免「快速遍历全部 provider
                # 再快速重来」的紧密重试风暴。首次失败不等待(保 fallback 的快速感),
                # 之后随尝试次数递增退避。
                if i > 0:
                    _t.sleep(_backoff_delay(i))
                continue                                   # → 试下一个已配 provider(fallback 链)
            self._cooldown.pop(provider, None)             # 成功 → 解冻
            self._rec_llm(provider, True, (_t.perf_counter() - _t0) * 1000.0)
            if output and getattr(self, "meter", None) is not None:
                try:
                    self.meter.measure_call(
                        system=system, messages=messages, output=output,
                        provider=provider, model=model, agent_id=agent_id, usage=usage)
                except Exception:
                    pass
            if i > 0:
                self.fallback_reason = f"主 provider 失败,已回退到 {provider}"
            # ── A 类修复:provider 返回空串 = 业务故障(内容过滤/截断/静默失败)──
            # 旧代码 `output or self._stub()` 会短路到 stub,而 stub 文案声称「已完成处理」
            # → 违反 base.py §二「真相优先」铁律,且该文本会经 base._store_memory 落入
            # 长期记忆,造成「一次故障 → 长期污染」(后续召回持续复读)。
            # 新代码:置 degraded 标志(上游据此禁写记忆),返回显式失败文本。
            if not (output or "").strip():
                self._mark_degraded(
                    f"provider={provider} model={model} 返回空响应"
                    f"(疑似内容过滤或 max_tokens 截断)")
                return ("【模型返回为空】本次未获得有效回复,任务未完成。"
                        "可能原因:内容被模型安全策略过滤,或 max_tokens 过小被截断。"
                        "建议:调整提问方式或提高 max_tokens 后重试。")
            return output
        # 全部 provider 都失败:显式失败 + 降级标志,**不再拼接 stub 成功文案**。
        # 保留「全部模型失败」这一精确措辞 —— 它是故障语义(而非"未配置模型"的降级),
        # 运维与测试都靠它识别真实故障。
        self._mark_degraded(f"全部 provider 调用失败: {last_err}")
        return (f"【全部模型失败】已尝试 {len(self._fallback_order())} 个 provider 均失败,"
                f"本次任务未完成。最后错误:"
                f"{type(last_err).__name__ if last_err else '未知'}。"
                f"请检查网络与 API Key 配置后重试。")

    @staticmethod
    def _rec_llm(provider, ok, ms):
        try:
            from .metrics import METRICS
            METRICS.record_llm(provider, ok, ms)
        except Exception:
            pass

    def stream(self, system: str, messages: list, max_tokens: int = 2048,
               agent_id: str = "", stop_event=None, images: list = None):
        """流式文本补全:逐块 yield。各 provider 用其流式 API;stub/异常降级分块。
        与 complete() 同为 LLM 咽喉,收尾做 token 计量。images(可选)= 多模态图片。
        stop_event:threading.Event,置位则提前终止(客户端断连时省 token)。"""
        def _stopped():
            return stop_event is not None and stop_event.is_set()
        system = self._inject_runtime(system)   # 规则化:与 complete 同一咽喉注入运行时事实基准
        if self.is_stub:
            try:
                from .metrics import METRICS
                METRICS.record_llm("stub", True, 0.0)
            except Exception:
                pass
            try:
                from .tracing import current_trace
                tr = current_trace()
                if tr is not None:
                    tr.add_span({"name": "llm.stream", "ok": True, "ms": 0.0,
                                 "meta": {"provider": "stub"}})
            except Exception:
                pass
            for w in _chunk_text(self._stub(messages)):
                if _stopped():
                    return
                yield w
            return
        parts = []
        import time as _t
        _t0 = _t.perf_counter()
        model = self._model_for(self.provider, agent_id, messages)   # 按任务/Agent 选档

        def _rec(ok):
            ms = (_t.perf_counter() - _t0) * 1000.0
            try:
                from .metrics import METRICS
                METRICS.record_llm(self.provider, ok, ms)
            except Exception:
                pass
            try:    # 链路追踪:流式是 chat 主路径,不能漏(span 归属发起请求的 trace)
                from .tracing import current_trace
                tr = current_trace()
                if tr is not None:
                    tr.add_span({"name": "llm.stream", "ok": ok, "ms": round(ms, 2),
                                 "meta": {"provider": self.provider, "model": model}})
            except Exception:
                pass
        mm = self._attach_images(self.provider, messages, images) if images else messages
        try:
            if self.provider == "anthropic":
                with self._client.messages.stream(
                        model=model, max_tokens=max_tokens,
                        system=system, messages=mm) as s:
                    for t in s.text_stream:
                        if _stopped():
                            return
                        if t:
                            parts.append(t); yield t
            elif self.provider in ("openai", "deepseek", "qwen", "local"):
                msgs = [{"role": "system", "content": system}] + mm
                st = self._client.chat.completions.create(
                    model=model, max_tokens=max_tokens, messages=msgs, stream=True)
                for c in st:
                    if _stopped():
                        st.close() if hasattr(st, "close") else None
                        return
                    d = c.choices[0].delta.content if c.choices else None
                    if d:
                        parts.append(d); yield d
            elif self.provider == "google":
                prompt = system + "\n\n" + "\n".join(m["content"] for m in messages)
                for c in self._client.generate_content(prompt, stream=True):
                    if _stopped():
                        return
                    if getattr(c, "text", ""):
                        parts.append(c.text); yield c.text
            else:
                out = self.complete(system, messages, max_tokens, agent_id)
                parts.append(out); yield out
        except Exception as e:
            _rec(False)
            # 脱敏(M1 A 类修复):不把原始异常对象吐给用户 —— 它可能含内部端点、
            # 文件路径或请求细节。只给异常类型 + 降级语义,完整原因进 tracing。
            self._mark_degraded(f"stream 失败: {type(e).__name__}")
            yield f"【模型流式中断】{type(e).__name__},本次未获得完整回复。\n"
            for w in _chunk_text(self._stub(messages)):
                yield w
            return
        _rec(True)
        output = "".join(parts)
        if output and getattr(self, "meter", None) is not None:
            try:
                self.meter.measure_call(
                    system=system, messages=messages, output=output,
                    provider=self.provider, model=self.model,
                    agent_id=agent_id, usage=None)
            except Exception:
                pass

    # ── P2 function-calling(工具调用)──────────────────────────────────────────
    # 病根修复:让模型**自己决定**调哪个工具,取代"调 LLM 前用关键词表拍板"。
    # 模型无关:openai 家族(openai/deepseek/qwen/local,含 openai-compatible 万能通道)走
    # OpenAI tools 格式;其它 provider(anthropic/google/stub)暂不支持 tools → supported=False,
    # 由上层自动回退关键词增强链(fallback,不破坏)。anthropic tool_use 留作后续扩展点。
    _OPENAI_FAMILY = ("openai", "deepseek", "qwen", "local")

    def supports_tools(self) -> bool:
        return (not self.is_stub) and self.provider in self._OPENAI_FAMILY

    def call_tools(self, system: str, messages: list, tools: list,
                   tool_choice: str = "auto", max_tokens: int = 1024,
                   agent_id: str = "") -> dict:
        """带工具的一次(非流式)LLM 调用。返回归一化结果:
          {supported: bool, text: str,
           tool_calls: [{id, name, args(dict)}],
           assistant_msg: dict}   # provider 原生的 assistant 消息,供多轮回灌时追加
        不支持 tools 的 provider → {supported: False,...},上层走 fallback。
        与 complete/stream 同一咽喉:注入运行时事实基准(否则模型会把日期猜成训练年份)。"""
        system = self._inject_runtime(system)
        if not self.supports_tools():
            return {"supported": False, "text": "", "tool_calls": [], "assistant_msg": None}
        model = self._model_for(self.provider, agent_id, messages)
        msgs = [{"role": "system", "content": system}] + list(messages)
        import time as _t
        _t0 = _t.perf_counter()
        try:
            from .tracing import span as _span
            with _span("llm.tools", provider=self.provider, model=model):
                resp = self._client.chat.completions.create(
                    model=model, max_tokens=max_tokens, messages=msgs,
                    tools=tools, tool_choice=tool_choice)
        except Exception as e:
            self._rec_llm(self.provider, False, (_t.perf_counter() - _t0) * 1000.0)
            # 工具调用失败(端点不支持 tools 等)→ 当作不支持,让上层降级
            return {"supported": False, "text": "", "tool_calls": [],
                    "assistant_msg": None, "error": str(e)}
        self._rec_llm(self.provider, True, (_t.perf_counter() - _t0) * 1000.0)
        m = resp.choices[0].message
        calls = []
        raw_tcs = []
        for tc in (getattr(m, "tool_calls", None) or []):
            import json as _json
            try:
                args = _json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            calls.append({"id": tc.id, "name": tc.function.name, "args": args})
            raw_tcs.append({"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name,
                                         "arguments": tc.function.arguments}})
        assistant_msg = {"role": "assistant", "content": m.content or ""}
        if raw_tcs:
            assistant_msg["tool_calls"] = raw_tcs
        # 计量(工具决策轮也算调用)
        if getattr(self, "meter", None) is not None:
            try:
                self.meter.measure_call(system=system, messages=messages,
                                        output=(m.content or ""), provider=self.provider,
                                        model=model, agent_id=agent_id, usage=None)
            except Exception:
                pass
        return {"supported": True, "text": m.content or "",
                "tool_calls": calls, "assistant_msg": assistant_msg}

    @staticmethod
    def format_tool_result(tool_call: dict, result: str) -> dict:
        """把一次工具执行结果封装成 provider 原生消息,供追加进 messages 再喂回模型。
        openai 家族:{role: tool, tool_call_id, content}。"""
        return {"role": "tool", "tool_call_id": tool_call.get("id", ""),
                "content": str(result)}

    def _stub(self, messages: list) -> str:
        """无模型可用时的降级回复(never_configured 场景的合理兜底)。

        诚实性契约(与 base.py §二「真相优先」对齐):
          · 措辞**不得**包含「已完成」「已处理」「已给出方案」等完成态动词
            —— 旧文案「已完成处理:检索相关记忆、分析要点…」把「什么都没做」
            包装成了「做完」,是假成功;
          · 必须显式告知未执行,并给出恢复动作;
          · 仍保留原 task_line 提取(贴合任务,便于 Harness 观察与用户识别)。
        注:故障降级(call_failed)路径**不再走本函数**,见 complete() 中 _mark_degraded 分支。
        """
        user_text = str(messages[-1]["content"]) if messages else ""
        task_line = user_text
        for marker in ["任务：", "财务任务：", "行程任务：", "学习任务：",
                       "请协助处理以下任务：", "请处理"]:
            if marker in user_text:
                task_line = user_text.split(marker, 1)[1]
                break
        task_line = task_line.strip().split("\n")[0][:80]
        return (f"【未执行】当前没有可用的模型服务,本次未进行推理,也没有执行任何工具。\n"
                f"待处理任务:「{task_line}」\n"
                f"恢复方式:配置任一模型 Key(见 .env.example)后重试。\n"
                f"(STUB · 此为降级占位,非真实结果)")

    # ── 降级状态(A 类修复)────────────────────────────────────────────
    def _mark_degraded(self, reason: str) -> None:
        """标记本次调用为「故障降级」(区别于 is_stub 的「从未配置」)。

        上游读 is_degraded() / take_call_degraded() 决定:
          · 是否禁止把本次回复写入长期记忆(故障文本会污染召回);
          · 是否回传告警(便于前端/监控识别「这次不是真实结果」)。
        观测手段(tracing)包在 try 里 —— 可观测性是增强项,不能因它失败影响主流程。
        """
        self.degraded = True
        self._call_degraded = True
        self.degraded_reason = ((self.degraded_reason + " | ")
                                if self.degraded_reason else "") + str(reason)
        try:
            from .tracing import span as _span
            with _span("llm.degraded", provider=self.provider, reason=str(reason)[:200]):
                pass
        except Exception:
            pass

    def is_degraded(self) -> bool:
        """本次(或最近一次)调用是否因**故障**降级。区别于 is_stub(从未配置)。"""
        return bool(self.degraded)

    def take_call_degraded(self) -> bool:
        """取出并清零 per-call 降级标记(供单次请求生命周期内消费)。

        per-call 而非全局:并发多请求时,只有**本次**真正故障的那个请求才该
        禁写记忆,不能因另一请求的故障误伤。
        """
        v = self._call_degraded
        self._call_degraded = False
        return v

    def switch(self, provider: str, model: str = None):
        """运行时切换 provider(多模型路由的核心能力)。
        显式给 model → 锁定该模型(不分档,尊重用户选择);只切 provider(model 空)→ 清锁用分档。"""
        self.provider = provider
        self._locked_model = (model or "").strip()   # 修复:显式模型必须生效(否则被 tiering 覆盖)
        self.model = self._locked_model or DEFAULT_MODELS.get(provider, self.model)
        self._init_client()
        return self.info()

    def info(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "active": not self.is_stub,
            # ── 降级可见性(A 类修复)────────────────────────────────
            # 原先只报 provider + active。当 provider=anthropic 但 SDK 缺失时,
            # 运维看到的是「配置了但没激活」,真实原因(SDK 未安装)被吞掉。
            # degraded / degraded_reason / fallback_reason / client_ready 四者一起,
            # 才说得清「为什么没模型可用」。
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason[:200],
            "fallback_reason": self.fallback_reason,
            "client_ready": self._client is not None,
            "available_providers": self.available_providers(),
        }

    @staticmethod
    def available_providers() -> list:
        """列出当前环境已配置 key 的 provider。"""
        out = []
        for p, env in PROVIDER_ENV.items():
            out.append({"provider": p, "configured": bool(os.environ.get(env)),
                        "default_model": DEFAULT_MODELS.get(p, "")})
        return out
