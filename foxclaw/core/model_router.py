"""
Foxclaw — 多模型路由(Model Router)
体现 Foxclaw 的核心架构哲学:认知层与 LLM 解耦,大模型只是可替换的文本生成器。
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
import json
import threading
import re as _re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


# ── BASE URL 规范化(自定义模型)─────────────────────────────────────────────
# 病根:用户按厂商文档填 `https://api.deepseek.com`(无 /v1),OpenAI SDK 会拼成
# `https://api.deepseek.com/chat/completions` —— 兼容网关多数只认 `/v1/chat/completions`,
# 于是 404/401;/v1 又常被"看文档照抄"的人漏掉。两边都不该让用户自己猜:
#   · 无 scheme → 补 https://
#   · path 为空或 "/" → 补 /v1
#   · 已有 path(/v1、/compatible-mode/v1、/openai/v1…)→ 原样保留
# 幂等:normalize(normalize(x)) == normalize(x)。
def normalize_base_url(url: str) -> str:
    """把用户填的 BASE URL 收拾成 SDK 可直接用的形式(带 scheme + 至少一段 path)。

    非法输入抛 ``ValueError``(空串 / 非 http(s) / 解析失败)。
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("BASE URL 不能为空")
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except Exception as e:                      # pragma: no cover - urlsplit 极少抛
        raise ValueError(f"BASE URL 解析失败: {e}")
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError("BASE URL 必须以 http:// 或 https:// 开头")
    if not parts.netloc:
        raise ValueError("BASE URL 缺少主机名(例:https://api.deepseek.com/v1)")
    path = (parts.path or "").rstrip("/")
    if not path:
        path = "/v1"
    return urlunsplit((parts.scheme.lower(), parts.netloc, path, parts.query, ""))


# ── 推理块剥离:openai-compatible 协议(DeepSeek-R1 / MiniMax-Text-01 等)把内部
# reasoning 嵌进 content 字段,标签是 OPEN_TAG ... CLOSE_TAG(也兼容 reasoning / reflection
# 变体)。流式一个字一个字 yield 时如果不在 LLM 咽喉处清理,会渗到前端 textContent /
# SSE 流量 / 落库记忆。统一在 complete() / stream() 输出端 strip 是最稳的 single source。
# ────────────────────────────────────────────────────────────────────
# 用字符串拼接定义正则:避免 think 标签在源码里裸出现时被 markdown / 编辑器误处理。
_THINK_RE = _re.compile("<" + "think" + ">.*?</" + "think" + ">", _re.DOTALL)
# 部分模型(DeepSeek-R1 早期 / minimax 变体)用 <reasoning> / <reflection> 标签
# 一并兼容,展示给用户时统一清掉。
_EXTRA_THINK_RE = _re.compile(r"<(reasoning|reflection|reflexion)>.*?</\1>", _re.DOTALL)


def _strip_think(s: str) -> str:
    """One-shot:整段(非流式)剥离 think 块 / reasoning 块等。"""
    if not s:
        return s
    s = _THINK_RE.sub("", s)
    s = _EXTRA_THINK_RE.sub("", s)
    return s.lstrip()


class _ThinkStripper:
    """Stateful:逐 chunk 喂入,返回**可以 yield 给用户**的部分;跨 chunk 状态保留,
    直到 think 块完整闭合,中间内容丢弃。

    为什么 stateful:模型流式可能在 chunk 边界切碎 think 标签(比如把 OPEN_TAG
    切到下个 chunk),若不在 LLM 咽喉处做跨 chunk 跟踪,部分 think 内容会泄露到
    前端 / 落库记忆 / SSE 流量。"""

    _OPEN_TAG = "<think>"
    _CLOSE_TAG = "</think>"

    def __init__(self):
        self._in_think = False
        self._buf = ""

    def feed(self, chunk: str) -> str:
        """输入新 chunk,返回可 yield 部分(已剥离 think 块)。"""
        return self.feed_events(chunk)[0]

    def feed_events(self, chunk: str) -> tuple[str, str]:
        """输入新 chunk,返回 (可见正文, 推理文本)。

        可见正文与 `feed` 逐字一致(think 块一律不进正文);推理文本是刚闭合的
        think 块内容,由上层作为独立 SSE `thinking` 事件送前端折叠展示——**正文仍净化**,
        思考不再被丢弃(此前整段淘汰,用户只看到结论)。
        """
        if not chunk:
            return "", ""
        out: list[str] = []
        think: list[str] = []
        text = self._buf + chunk
        self._buf = ""
        i = 0
        n = len(text)
        while i < n:
            if self._in_think:
                end = text.find(self._CLOSE_TAG, i)
                if end == -1:
                    # 没遇到结束标签,剩余暂存,等下个 chunk 续上
                    self._buf = text[i:]
                    return "".join(out), "".join(think)
                think.append(text[i:end])
                i = end + len(self._CLOSE_TAG)
                self._in_think = False
                continue
            else:
                start = text.find(self._OPEN_TAG, i)
                if start == -1:
                    # 当前 chunk 没有 <think> 开始标签。
                    # 但末尾可能正好切成 "<" / "<th" / "<thi" ... —— 不能直接
                    # 整段 yield,否则下个 chunk 拼成 "<think>..." 时已经晚一步。
                    # 策略:把最后一个 '<' 之前的安全段 yield,'<' 及其后暂存到 buf。
                    last_lt = text.rfind("<", i)
                    if last_lt == -1:
                        out.append(text[i:])
                        return "".join(out), "".join(think)
                    out.append(text[i:last_lt])
                    self._buf = text[last_lt:]
                    return "".join(out), "".join(think)
                out.append(text[i:start])
                i = start + len(self._OPEN_TAG)
                self._in_think = True
        return "".join(out), "".join(think)


# 各 provider 的默认模型(可被环境变量覆盖)
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai":    "gpt-4o",
    "deepseek":  "deepseek-chat",
    "google":    "gemini-1.5-pro",
    "qwen":      "qwen-plus",
    "local":     "llama3",
    "minimax":   "MiniMax-Text-01",
    "generic":   "",   # 通用 openai-compatible:必须显式给 model(.env: FOXCLAW_MODEL 或 GENERIC_LLM_MODEL)
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
    default = os.environ.get("FOXCLAW_ROUTE_DEFAULT_TIER", "smart").strip().lower()
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
    "minimax":   "MINIMAX_API_KEY",  # MiniMax 官方 API;鉴权还需 MINIMAX_GROUP_ID
    "generic":   "LLM_API_KEY",           # 【P2·单 LLM 入口】通用 openai-compatible;鉴权见 LLM_EXTRA_HEADERS
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
from . import settings

# 冷却基准秒(乘抖动系数后使用)。可经 FOXCLAW_LLM_COOLDOWN 覆盖,线上调参不必改代码。
_COOLDOWN_BASE = float(settings.get_float("FOXCLAW_LLM_COOLDOWN"))


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
        # 【P2·单 LLM 入口】FOXCLAW_MODEL 已弃用,改读 LLM_MODEL(空值时回退默认模型)
        _env_model = os.environ.get("LLM_MODEL", "").strip()
        self._locked_model = model or _env_model or ""   # 显式锁定 → 不分档(向后兼容)
        self.model = self._locked_model or DEFAULT_MODELS.get(self.provider, "")
        self._client = None
        self._clients = {}                               # provider -> client(fallback 懒建缓存)
        self._client_lock = threading.Lock()
        self._cooldown = {}                              # provider -> 冷却截止 monotonic(断路器:跳过刚宕机的家)
        self.fallback_reason = ""
        self.meter = None        # 可选 TokenMeter,由 Foxclaw 注入
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
        # 前端 LLM 管理选模型(写 .current_model → mtime 缓存 lazy reload,无需重启)
        self._selection_mtime: float = 0.0
        self._init_client()

    @staticmethod
    def _auto_detect() -> str:
        """选择 provider:优先 LLM_PROVIDER 显式指定,否则按优先级探测已配 key 的。"""
        # 1. 显式指定(LLM_PROVIDER=deepseek 即可强制用 DeepSeek;兼容旧名 FOXCLAW_PROVIDER)
        forced = (os.environ.get("LLM_PROVIDER", "")
                  or os.environ.get("FOXCLAW_PROVIDER", "")).strip().lower()
        if forced and forced in PROVIDER_ENV:
            return forced
        # 2. 自动探测:按优先级找第一个配了 key 的
        for p in ["anthropic", "openai", "deepseek", "google", "qwen", "local", "minimax", "generic"]:
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
            elif p in ("openai", "deepseek", "qwen", "local", "minimax", "generic"):
                # ── OpenAI-compatible 家族 ───────────────────────────
                # 适配所有 OpenAI Chat Completion 协议兼容厂商。
                # 鉴权默认 Bearer(api_key);特殊厂商(双 header / 自定义鉴权)通过
                # default_headers 注入。generic 走完全 .env 驱动,任意厂商即插即用。
                try:
                    from openai import OpenAI
                except ImportError:
                    return None, "需安装 openai 库(pip install openai)"
                extra = {}     # default_headers(双 header 鉴权用,例 MiniMax-Group-Id)
                if p == "openai":
                    key = os.environ.get("OPENAI_API_KEY"); base = None
                elif p == "deepseek":
                    key = os.environ.get("DEEPSEEK_API_KEY"); base = "https://api.deepseek.com/v1"
                elif p == "qwen":
                    key = os.environ.get("DASHSCOPE_API_KEY")
                    base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
                elif p == "local":
                    key = os.environ.get("LOCAL_LLM_KEY", "local")
                    base = os.environ.get("LOCAL_LLM_BASE", "http://localhost:11434/v1")
                elif p == "minimax":
                    # 官方域名,可用 MINIMAX_BASE_URL 自定义代理;group_id 可选(无则不传 header)
                    key = os.environ.get("MINIMAX_API_KEY")
                    base = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
                    group_id = os.environ.get("MINIMAX_GROUP_ID", "").strip()
                    if group_id:
                        extra = {"MiniMax-Group-Id": group_id}
                else:  # generic
                    # 【P2·单 LLM 入口】通用 openai-compatible:适配任意兼容厂商(MiniMax/GLM/Kimi/自建网关/三方代理)
                    # 必填:LLM_API_KEY + LLM_BASE_URL
                    # 可选:LLM_EXTRA_HEADERS(JSON object,适配双 header 鉴权)
                    key = os.environ.get("LLM_API_KEY") or os.environ.get("GENERIC_LLM_API_KEY")
                    base = os.environ.get("LLM_BASE_URL") or os.environ.get("GENERIC_LLM_BASE_URL")
                    raw = os.environ.get("LLM_EXTRA_HEADERS", "").strip() \
                          or os.environ.get("GENERIC_LLM_EXTRA_HEADERS", "").strip()
                    if raw:
                        import json as _json
                        try:
                            parsed = _json.loads(raw)
                        except Exception as e:
                            return None, f"GENERIC_LLM_EXTRA_HEADERS 不是合法 JSON: {e}"
                        if not isinstance(parsed, dict):
                            return None, "GENERIC_LLM_EXTRA_HEADERS 必须是 JSON object(例: {\"X-Foo\": \"bar\"})"
                        extra = {str(k): str(v) for k, v in parsed.items()}
                if not (key and base):
                    return None, f"未配置 {p} 的 API Key 或 BASE_URL(检查 .env)"
                return (OpenAI(api_key=key, base_url=base, timeout=30.0, max_retries=2,
                               default_headers=extra) if extra else
                        OpenAI(api_key=key, base_url=base, timeout=30.0, max_retries=2),
                        "")
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
        for p in ["anthropic", "openai", "deepseek", "google", "qwen", "local", "minimax", "generic"]:
            if p != self.provider and os.environ.get(PROVIDER_ENV.get(p, ""), ""):
                order.append(p)
        return order

    # ── 前端 LLM 管理(写 .current_model → mtime 缓存 lazy reload)────────────────
    # 配套文件 FOXCLAW_DATA_DIR/.current_model(由 foxclaw/api/routes/llm_admin.py 写)。
    # 改动走 mtime 比对,正常 chat 路径上每请求 < 1ms;切完模型不用重启 foxclaw。

    def _selection_path(self) -> "Path":
        return Path(os.environ.get("FOXCLAW_DATA_DIR", "./foxclaw_data")) / ".current_model"

    def _custom_models_path(self) -> "Path":
        return Path(os.environ.get("FOXCLAW_DATA_DIR", "./foxclaw_data")) / "custom_models.json"

    def _build_custom_client(self, name: str):
        """从 ``custom_models.json`` 读 + 构造 OpenAI client。
        返回 ``(client_or_None, model, error_str)``。"""
        import base64 as _b64
        p = self._custom_models_path()
        if not p.exists():
            return None, "", "custom_models.json 不存在"
        try:
            models = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            return None, "", f"读 custom_models.json 失败: {e}"
        match = next((m for m in models if m.get("name") == name), None)
        if not match:
            return None, "", f"未找到自定义模型: {name}"
        # key 反混淆(base64);兼容旧的明文字段
        key_obf = match.get("api_key_obf", "") or ""
        key = ""
        if key_obf:
            try:
                key = _b64.b64decode(key_obf.encode()).decode()
            except Exception:
                key = ""
        if not key:
            key = match.get("api_key", "") or ""
        if not key:
            return None, "", "API key 为空"
        # 老记录可能存成不带 /v1 的形式 → 构造时统一规范化(与 llm_admin 新增/编辑同源)
        try:
            base_url = normalize_base_url(match.get("base_url", ""))
        except ValueError as e:
            return None, "", f"BASE URL 非法: {e}"
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=key,
                base_url=base_url,
                timeout=30.0,
                max_retries=2,
                default_headers=match.get("extra_headers") or {},
            )
        except Exception as e:
            return None, "", f"构造 client 失败: {e}"
        return client, match.get("model", ""), ""

    def _maybe_reload_selection(self):
        """检查 ``.current_model`` 文件;mtime 变更即重建 active client。

        副作用:
          - 文件不存在 → 退回 ``_auto_detect()`` 探测的 provider(与 .env 一致)。
          - ``source=preset`` → 切到对应 provider(``_init_client`` 重建)。
          - ``source=custom`` → 构造 custom OpenAI client,挂到 ``self._clients["custom"]``,
            同步 ``self.provider = "custom"`` / ``self.model``。"""
        p = self._selection_path()
        try:
            mtime = p.stat().st_mtime if p.exists() else 0.0
        except Exception:
            mtime = 0.0
        if mtime == self._selection_mtime:
            return                                  # 没变 → 零开销
        self._selection_mtime = mtime
        if not p.exists():
            # 删了 .current_model → 退回 .env 探测
            env_prov = self._auto_detect()
            if env_prov != self.provider:
                self.provider = env_prov
                self._reset_locked_for_provider()
                self._init_client()
            return
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(d, dict):
            return
        src = d.get("source")
        if src == "preset" and d.get("provider"):
            new_prov = d["provider"]
            if new_prov != self.provider:
                self.provider = new_prov
                self._reset_locked_for_provider()    # 切 provider:FOXCLAW_MODEL 仅 generic-like 适用
                self._init_client()                 # 重建 _client
        elif src == "custom":
            name = d.get("name", "")
            client, model, err = self._build_custom_client(name)
            if client is not None:
                self.provider = "custom"
                with self._client_lock:
                    self._clients["custom"] = client
                self._client = client              # 同步 active
                self.model = model
                self._locked_model = model
                self.fallback_reason = ""
            else:
                # 构造失败:log + 保持旧 provider(不静默吞)
                import sys as _sys
                print(f"[model_router] 自定义模型构造失败: {err}", file=_sys.stderr)

    def _reset_locked_for_provider(self):
        """切换 provider 时重置 ``_locked_model`` / ``self.model``。

        ``FOXCLAW_MODEL`` 是在配 generic/minimax/custom 时显式设的,不应污染切到
        anthropic/openai/local 等有固定默认的 provider(否则拿 MiniMax-Text-01 去
        ollama 调 → 404)。"""
        _env = os.environ.get("FOXCLAW_MODEL", "").strip()
        if self.provider in ("generic", "minimax", "custom"):
            # 这些 provider 没固定默认,FOXCLAW_MODEL 适用
            self._locked_model = _env or None
        else:
            # 其他 provider 用自己默认,忽略 FOXCLAW_MODEL 干扰
            self._locked_model = None
        self.model = self._locked_model or DEFAULT_MODELS.get(self.provider, "")

    def _model_for(self, provider: str, agent_id: str, messages: list) -> str:
        """按任务/Agent 选该 provider 的模型:锁定则用锁定值;否则按档。active provider 支持
        FOXCLAW_MODEL_FAST/SMART env 覆盖。generic 必须显式给 model(.env 任意一个都行)。"""
        if self._locked_model and provider == self.provider:
            return self._locked_model
        tier = _route_tier(agent_id, messages)
        if provider == self.provider:
            if provider == "generic":
                # generic 没默认 model → 必须 .env 显式给;优先级 FOXCLAW_MODEL > GENERIC_LLM_MODEL
                m = (os.environ.get("FOXCLAW_MODEL", "").strip() or
                     os.environ.get("GENERIC_LLM_MODEL", "").strip())
                if m:
                    return m
            env = os.environ.get("FOXCLAW_MODEL_FAST" if tier == "fast" else "FOXCLAW_MODEL_SMART", "").strip()
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
        elif provider in ModelRouter._OPENAI_FAMILY:
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
        elif provider in self._OPENAI_FAMILY:
            msgs = [{"role": "system", "content": system}] + messages
            resp = client.chat.completions.create(model=model, max_tokens=max_tokens, messages=msgs)
            u = getattr(resp, "usage", None)
            usage = ({"input_tokens": getattr(u, "prompt_tokens", None),
                      "output_tokens": getattr(u, "completion_tokens", None)} if u else None)
            # 剥离内部推理块(DeepSeek-R1 / MiniMax-Text-01 / minimax-reasoner 等
            # 把 <think>...</think> 嵌进 content,直接落库/转发会污染用户界面与记忆)
            return _strip_think(resp.choices[0].message.content or ""), usage
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
        self._maybe_reload_selection()           # 前端切模型(.current_model)→ mtime 缓存 lazy reload
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
               agent_id: str = "", stop_event=None, images: list = None,
               on_thinking=None):
        """流式文本补全:逐块 yield。各 provider 用其流式 API;stub/异常降级分块。
        与 complete() 同为 LLM 咽喉,收尾做 token 计量。images(可选)= 多模态图片。
        stop_event:threading.Event,置位则提前终止(客户端断连时省 token)。
        on_thinking(可选)= 推理增量回调:provider 原生 reasoning(reasoning_content)
        与正文内嵌 <think> 块都从这里出去,作为独立 SSE thinking 事件;正文不受影响。"""
        def _stopped():
            return stop_event is not None and stop_event.is_set()
        # 前端切模型(.current_model)→ mtime 缓存 lazy reload。
        # 病根:此前只有 complete() 里 reload,而聊天主路径是 stream() —— 用户在"模型选择器"
        # 里切完自定义模型后,流式对话仍拿旧 provider/旧 client(表现为"填了不生效")。
        self._maybe_reload_selection()
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
            elif self.provider in self._OPENAI_FAMILY:
                # P1 阶段的回归修复:此处原本只列了 openai/deepseek/qwen/local,
                # generic/minimax 走 else 分支退化为 complete() 一次性 yield
                # —— 既不是真"流式",也把
                # 这里使用 _OPENAI_FAMILY 与 supports_tools / tools 同步,保证
                # 任何 openai-compatible 厂商(minimax / generic / custom 等)都走
                # 真正的逐 token 流 + 在 LLM 咽喉处统一剥离 think 块。
                msgs = [{"role": "system", "content": system}] + mm
                st = self._client.chat.completions.create(
                    model=model, max_tokens=max_tokens, messages=msgs, stream=True)
                stripper = _ThinkStripper()
                for c in st:
                    if _stopped():
                        st.close() if hasattr(st, "close") else None
                        return
                    d = c.choices[0].delta.content if c.choices else None
                    # 原生推理通道:deepseek-reasoner / 部分 openai-compatible 厂商把思考放在
                    # delta.reasoning_content(少数用 reasoning)。此前整段被丢弃(只读 content),
                    # 用户完全看不到推理;现在转成 thinking 事件,正文仍只走 content。
                    if c.choices and on_thinking is not None:
                        _delta = c.choices[0].delta
                        _rt = (getattr(_delta, "reasoning_content", None)
                               or getattr(_delta, "reasoning", None))
                        if _rt:
                            on_thinking(_rt)
                    if not d:
                        continue
                    parts.append(d)
                    cleaned, _think_txt = stripper.feed_events(d)
                    if _think_txt and on_thinking is not None:
                        on_thinking(_think_txt)      # 内嵌 <think> 块 → 推理事件,不进正文
                    if cleaned:
                        yield cleaned
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
    # OpenAI tools 格式;anthropic 走原生 tool_use block(P0-2);google/stub 不支持 →
    # supported=False,由上层自动回退关键词增强链(fallback,不破坏)。
    # ⚠ "custom"(前端选中的自定义模型)必须在这里 —— 它是**运行时**才出现的 openai-compatible
    # 通道,漏掉会让 stream() 退化为 complete() 一次性输出(前端表现为"不流式"),且
    # supports_tools() 变 False → 工具体系静默降级。凡新增 openai 兼容 provider,
    # 只改这一处(调用点已统一引用本元组)。
    _OPENAI_FAMILY = ("openai", "deepseek", "qwen", "local", "generic", "minimax", "custom")
    _TOOL_PROVIDERS = _OPENAI_FAMILY + ("anthropic",)

    def supports_tools(self) -> bool:
        return (not self.is_stub) and self.provider in self._TOOL_PROVIDERS

    @staticmethod
    def _to_anthropic_tools(tools: list) -> list:
        """OpenAI tools 格式 → Anthropic tools 格式。

        OpenAI:  {type: "function", function: {name, description, parameters}}
        Anthropic:{name, description, input_schema}
        """
        out = []
        for t in tools or []:
            if not isinstance(t, dict):
                continue
            fn = t.get("function") if t.get("type") == "function" else t
            if not isinstance(fn, dict):
                continue
            name = fn.get("name")
            if not name:
                continue
            out.append({
                "name": name,
                "description": fn.get("description", "") or "",
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return out

    @staticmethod
    def _to_anthropic_tool_choice(tool_choice):
        """OpenAI tool_choice → Anthropic tool_choice。
        auto→{type:auto};none→{type:none};required→{type:any};
        {type:function,function:{name}}→{type:tool,name}。"""
        if tool_choice in (None, "auto"):
            return {"type": "auto"}
        if tool_choice == "none":
            return {"type": "none"}
        if tool_choice == "required":
            return {"type": "any"}
        if isinstance(tool_choice, dict):
            fn = tool_choice.get("function") or {}
            name = fn.get("name")
            if name:
                return {"type": "tool", "name": name}
        return {"type": "auto"}

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
        import time as _t
        _t0 = _t.perf_counter()
        try:
            from .tracing import span as _span
            with _span("llm.tools", provider=self.provider, model=model):
                if self.provider == "anthropic":
                    text, calls, assistant_msg = self._call_anthropic_tools(
                        model, system, messages, tools, tool_choice, max_tokens)
                else:
                    text, calls, assistant_msg = self._call_openai_tools(
                        model, system, messages, tools, tool_choice, max_tokens)
        except Exception as e:
            self._rec_llm(self.provider, False, (_t.perf_counter() - _t0) * 1000.0)
            # 工具调用失败(端点不支持 tools 等)→ 当作不支持,让上层降级
            return {"supported": False, "text": "", "tool_calls": [],
                    "assistant_msg": None, "error": str(e)}
        self._rec_llm(self.provider, True, (_t.perf_counter() - _t0) * 1000.0)
        # 计量(工具决策轮也算调用)
        if getattr(self, "meter", None) is not None:
            try:
                self.meter.measure_call(system=system, messages=messages,
                                        output=text, provider=self.provider,
                                        model=model, agent_id=agent_id, usage=None)
            except Exception:
                pass
        return {"supported": True, "text": text,
                "tool_calls": calls, "assistant_msg": assistant_msg}

    def _call_openai_tools(self, model, system, messages, tools, tool_choice, max_tokens):
        """openai 家族:chat.completions.create + tools,解析 tool_calls 为归一化结构。
        返回 (text, calls, assistant_msg)。"""
        msgs = [{"role": "system", "content": system}] + list(messages)
        resp = self._client.chat.completions.create(
            model=model, max_tokens=max_tokens, messages=msgs,
            tools=tools, tool_choice=tool_choice)
        m = resp.choices[0].message
        calls = []
        raw_tcs = []
        for tc in (getattr(m, "tool_calls", None) or []):
            import json as _json
            raw_args = tc.function.arguments or "{}"
            parse_failed = False
            try:
                args = _json.loads(raw_args)
                if not isinstance(args, dict):
                    # 模型偶发返回数组/标量(JSON 合法但结构不符)→ 同样按解析失败处理
                    args, parse_failed = {}, True
            except Exception:
                args, parse_failed = {}, True
            if parse_failed:
                # 静默 {} 会让模型以为工具已执行(实际参数全丢)→ 错误必须可见。
                # 此处**保留空 args 继续分发**:由 base._dispatch_tool 的参数校验闸
                # 把错误回注给模型,下一轮即可自修正;这里额外记 span,便于事后归因
                # (此前完全无痕迹,模型错了都不知道)。
                try:
                    from .tracing import span as _span
                    with _span("llm.tool_args_unparsable",
                               tool=tc.function.name, raw=str(raw_args)[:200]):
                        pass
                except Exception:
                    pass
            calls.append({"id": tc.id, "name": tc.function.name, "args": args,
                          "args_parse_failed": parse_failed})
            raw_tcs.append({"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name,
                                         "arguments": tc.function.arguments}})
        assistant_msg = {"role": "assistant", "content": m.content or ""}
        if raw_tcs:
            assistant_msg["tool_calls"] = raw_tcs
        return (m.content or ""), calls, assistant_msg

    def _call_anthropic_tools(self, model, system, messages, tools, tool_choice, max_tokens):
        """anthropic 原生 tool_use:openai tools 格式 → anthropic 格式,调 messages.create,
        解析 content block(tool_use 的 input 是原生结构化 dict,无需 JSON 解析)。
        返回 (text, calls, assistant_msg)。
        tool_choice=none → 映射 {type:none},但**仍传 tools**:anthropic 要求历史含 tool_use
        block 时请求必须带 tools 定义,否则报错(收敛轮的历史里必有 tool_use)。"""
        kwargs = {}
        anthropic_tools = self._to_anthropic_tools(tools)
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
        kwargs["tool_choice"] = self._to_anthropic_tool_choice(tool_choice)
        resp = self._client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=messages, **kwargs)
        text_parts = []
        calls = []
        blocks = []
        for b in (resp.content or []):
            t = getattr(b, "type", "")
            if t == "text":
                txt = getattr(b, "text", "") or ""
                text_parts.append(txt)
                blocks.append({"type": "text", "text": txt})
            elif t == "tool_use":
                args = getattr(b, "input", None) or {}
                calls.append({"id": getattr(b, "id", ""), "name": getattr(b, "name", ""),
                              "args": args, "args_parse_failed": False})
                blocks.append({"type": "tool_use", "id": getattr(b, "id", ""),
                               "name": getattr(b, "name", ""), "input": args})
        text = "".join(text_parts)
        assistant_msg = {"role": "assistant", "content": blocks}
        return text, calls, assistant_msg

    def format_tool_result(self, tool_call: dict, result: str) -> dict:
        """把一次工具执行结果封装成 provider 原生消息,供追加进 messages 再喂回模型。
        openai 家族:{role: tool, tool_call_id, content};
        anthropic:{role: user, content:[{type: tool_result, tool_use_id, content}]}。"""
        if self.provider == "anthropic":
            return {"role": "user", "content": [
                {"type": "tool_result",
                 "tool_use_id": tool_call.get("id", ""),
                 "content": str(result)}]}
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
