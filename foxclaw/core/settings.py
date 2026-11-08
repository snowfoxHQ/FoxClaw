"""环境变量配置:类型化读取 + 启动前校验 + 可操作提示。

## 为什么需要这个模块

配置散落在 20 多个文件、135 处 `os.environ.get(...)` 里。缺项时大多有默认值(不崩),
但**类型写错会崩在很远的地方**,而且崩得毫无线索:

    FOXCLAW_PORT=80oo
    → ValueError: invalid literal for int() with base 10: '80oo'
      File ".../foxclaw/api/middleware.py", line 173, in <module>

用户看到的是 `middleware.py` 第 173 行 —— 但问题在他自己的 `.env` 第 12 行,而且真正
想改的可能是 `FOXCLAW_PORT` 之外的另一处。另一种更常见的坏情况是**根本不崩**:
`FOXCLAW_TOOL_TIMEOUTS=60`(多打一个 S)永远不会报错,只是那个设置悄悄不生效。

所以这里做三件事:

1. **类型化读取**(`get_int` / `get_float`):解析失败时抛 `ConfigError`,消息里带
   「变量名 + 你写的原值 + 期望格式 + 示例 + 是哪个文件第几行在读它」。
2. **`check()` 一次性汇总所有问题**:不 fail-fast,一次把该改的都列出来;区分 error
   (会崩/意图破)与 warn(可疑但能跑)。
3. **未知键探测**:扫描包内源码,收集真正被读取的变量名;`.env` 里出现同前缀但没人读
   的键 → 大概率拼错,给出最接近的正确名(difflib)。

## 刻意不做的事

- **不统一布尔语义**。现有各处写法不一致且都有意为之:
  `url_policy` 是 `in ("1","true","yes")`(默认关),`web_search` 是
  `not in ("0","false","False","")`(默认开)。强行统一 = 静默改变已有行为,
  这类"顺手的重构"是回归温床,不动。
- **不改任何默认值、不接管 `os.environ`**。`get_*` 是纯读函数,不写回环境、不做缓存
  (运行期改环境变量仍然即时生效,与现状一致)。
- **不把配置抽成全局 settings 对象**。那需要改动所有调用点,收益只是"好看",
  风险却是整条启动链路。
"""
from __future__ import annotations

import ast
import inspect
import logging
import os
from dataclasses import dataclass
from difflib import get_close_matches
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# provider → 需要的环境变量(与 core/model_router.PROVIDER_ENV 保持一致;
# 这里重复一份是为了让 settings 不依赖 model_router —— 那会拖入 LLM SDK 的导入)。
PROVIDER_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "google": "GOOGLE_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "local": "LOCAL_LLM_BASE",
    "minimax": "MINIMAX_API_KEY",
    "generic": "LLM_API_KEY",
}

# 只检查这些前缀下的"未知键":它们属于本项目或本项目专用的厂商约定,
# 出现没人读的名字几乎必然是拼错。像 `PATH` / `HOME` 这类系统变量不在此列。
OWNED_PREFIXES = (
    "FOXCLAW_", "BAIDU_", "AMAP_", "LLM_", "GENERIC_LLM_",
    "MINIMAX_", "LOCAL_LLM_", "HKC_", "HMR_",
)


class ConfigError(ValueError):
    """某个环境变量的值不合法。消息本身就是给用户看的修复指引。"""


@dataclass(frozen=True)
class Opt:
    """一个环境变量的声明。kind 只描述**解析方式**,不代表业务语义。"""

    name: str
    kind: str = "str"                  # str | int | float | csv | choice
    default: Any = ""
    group: str = "其他"
    hint: str = ""                     # 期望格式 / 示例(拼进出错信息)
    choices: Sequence[str] = ()
    secret: bool = False
    lo: float | None = None
    hi: float | None = None


SPECS: tuple[Opt, ...] = (
    # lo/hi 只给**调用点自己不兜底**的项。像 TRACE_RETAIN 已有 max(100, ...)、
    # BAIDU_SEARCH_TIMEOUT 已有 max(5, ...):在那里再报一次范围错,等于把"clamp"
    # 变成"整条配置作废退默认",反而更糟。
    # ── 模型 ────────────────────────────────────────────────────────────────
    Opt("LLM_PROVIDER", "choice", "", "模型",
        "anthropic / openai / deepseek / google / qwen / local / minimax / generic",
        tuple(PROVIDER_KEY_ENV)),
    Opt("FOXCLAW_PROVIDER", "choice", "", "模型", "旧名,同上(优先用 LLM_PROVIDER)",
        tuple(PROVIDER_KEY_ENV)),
    Opt("LLM_MODEL", "str", "", "模型", "具体模型名,留空用该 provider 的默认模型"),
    Opt("FOXCLAW_MODEL", "str", "", "模型", "已弃用,改用 LLM_MODEL"),
    Opt("FOXCLAW_LLM_COOLDOWN", "float", 20.0, "模型",
        "秒,某 provider 失败后的冷却基准", lo=0),
    Opt("DEEPSEEK_API_KEY", "str", "", "模型", "DeepSeek 的 key", secret=True),
    Opt("ANTHROPIC_API_KEY", "str", "", "模型", "Anthropic 的 key", secret=True),
    Opt("OPENAI_API_KEY", "str", "", "模型", "OpenAI 的 key", secret=True),
    Opt("GOOGLE_API_KEY", "str", "", "模型", "Google Gemini 的 key", secret=True),
    Opt("DASHSCOPE_API_KEY", "str", "", "模型", "阿里云百炼(通义)的 key", secret=True),
    Opt("MINIMAX_API_KEY", "str", "", "模型", "MiniMax 的 key", secret=True),
    Opt("MINIMAX_GROUP_ID", "str", "", "模型", "MiniMax 的 group id(与 key 配对)"),
    Opt("LLM_API_KEY", "str", "", "模型", "generic(通用 openai 兼容)的 key", secret=True),
    Opt("LLM_BASE_URL", "str", "", "模型", "generic 的 base_url,如 https://host/v1"),
    Opt("LOCAL_LLM_BASE", "str", "", "模型", "本地推理服务的 base_url"),
    # ── 服务 ────────────────────────────────────────────────────────────────
    Opt("FOXCLAW_PORT", "int", 8000, "服务", "HTTP 监听端口", lo=1, hi=65535),
    Opt("FOXCLAW_DATA_DIR", "str", "", "服务", "数据目录(默认 <仓库>/foxclaw_data)"),
    Opt("FOXCLAW_IMPORT_ROOTS", "csv", "", "服务",
        "额外允许的导入目录(逗号分隔);不配则只有数据目录内的文件可被导入"),
    Opt("FOXCLAW_API_KEY", "str", "", "服务",
        "对外访问口令;设了才启用鉴权(空 = 仅本机开放)", secret=True),
    Opt("FOXCLAW_CORS_ORIGINS", "str", "", "服务",
        "逗号分隔的来源;留空只放行回环,\"*\" 全开(危险)"),
    Opt("FOXCLAW_ALLOWED_HOSTS", "csv", "", "服务", "逗号分隔的 Host 白名单"),
    Opt("FOXCLAW_GATEWAY", "choice", "", "服务", "1 强制开网关限流 / 0 强制关",
        ("0", "1")),
    Opt("FOXCLAW_GATEWAY_RATE", "int", 600, "服务", "每客户端每分钟请求数", lo=0),
    Opt("FOXCLAW_GATEWAY_QUOTA", "int", 20000, "服务", "每客户端每小时配额", lo=0),
    Opt("FOXCLAW_MAX_BODY_MB", "int", 32, "服务", "请求体上限(MB)", lo=1),
    Opt("FOXCLAW_STREAM_RATE", "int", 60, "服务", "流式端点每分钟上限", lo=0),
    Opt("FOXCLAW_STREAM_CONCURRENCY", "int", 4, "服务", "流式并发上限", lo=1),
    Opt("FOXCLAW_SHADOW_ROUNDS", "int", 20, "服务", "影子推演轮数", lo=1),
    # ── 工具 / 追踪 ─────────────────────────────────────────────────────────
    Opt("FOXCLAW_TOOL_TIMEOUT", "float", 45.0, "工具",
        "单个工具调用硬超时(秒);可小于 1(测试用),调用点会兜 >0"),
    Opt("FOXCLAW_TRACE_SLOW_MS", "float", 1000.0, "追踪", "超过多少毫秒算慢请求", lo=0),
    Opt("FOXCLAW_TRACE_SAMPLE", "float", 0.0, "追踪",
        "非慢请求的采样率 0~1;超出范围由调用点 clamp,不在这里报错"),
    Opt("FOXCLAW_TRACE_RETAIN", "int", 5000, "追踪",
        "内存里保留的 trace 条数;调用点已有 max(100, ...) 兜底"),
    # ── 搜索 ────────────────────────────────────────────────────────────────
    Opt("BAIDU_SEARCH_KEY", "str", "", "搜索", "百度 AI 搜索的 key", secret=True),
    Opt("BAIDU_SEARCH_MODEL", "str", "", "搜索", "百度搜索用的模型名"),
    Opt("BAIDU_SEARCH_BASE", "str", "", "搜索", "百度搜索的 base_url"),
    Opt("BAIDU_SEARCH_TIMEOUT", "int", 45, "搜索", "秒;调用点已有 max(5, ...) 兜底"),
    # ── 渠道 ────────────────────────────────────────────────────────────────
    Opt("FOXCLAW_CHANNEL_TRUSTED_SENDERS", "csv", "", "渠道",
        "逗号分隔的可信发送者:<id> / <channel>:<id> / <channel>:*"),
    Opt("FOXCLAW_CHANNEL_TRUST_ALL", "str", "", "渠道",
        "1/true 才让裸 `*` 生效(全局信任,不推荐)"),
    Opt("FOXCLAW_CHANNEL_SIGNING_SECRET", "str", "", "渠道",
        "出站 HMAC 密钥;留空 = 不签", secret=True),
    Opt("FOXCLAW_CHANNEL_SIGNING_KEY_ID", "str", "", "渠道", "密钥轮换标识"),
    # ── 浏览器 ──────────────────────────────────────────────────────────────
    Opt("FOXCLAW_BROWSER_ALLOW_PRIVATE", "str", "", "浏览器",
        "1/true/yes 才允许访问内网地址"),
)

SPEC_BY_NAME: dict[str, Opt] = {o.name: o for o in SPECS}

# 会在启动/运行早期被读、且类型写错就会崩的少数几个 —— 供 serve 预检快速拦截。
FATAL_CODES = ("type", "choice", "range")


@dataclass
class Issue:
    level: str                 # "error" | "warn"
    code: str                  # type / choice / range / provider_key / unknown_key ...
    name: str
    message: str
    hint: str = ""             # 修复建议(可为空)

    def __str__(self) -> str:
        # 只用 ASCII 记号:Windows 控制台默认 GBK,✓/✗/⚠ 会抛 UnicodeEncodeError
        mark = "ERROR" if self.level == "error" else " WARN"
        head = f"[{mark}] {self.name}: {self.message}" if self.name else f"[{mark}] {self.message}"
        return f"{head}\n          → {self.hint}" if self.hint else head


def _caller() -> str:
    """当前 `get_*` 的调用点(文件:行)。只在出错路径上求值,不进热路径。"""
    try:
        f = inspect.currentframe()
        for _ in range(3):                      # 跳过 get_* → _parse → _caller 自身
            if f is None:
                return ""
            f = f.f_back
        if f is None:
            return ""
        return f"{Path(f.f_code.co_filename).name}:{f.f_lineno}"
    except Exception:                            # noqa: BLE001  诊断信息不值得再抛一次
        return ""


def _parse(opt: Opt, raw: str, where: str = "") -> Any:
    """把原始字符串转成目标类型;不合法抛 ConfigError(消息可直接给用户看)。"""
    text = raw.strip()
    expect = opt.hint or opt.kind
    suffix = f"(错误位置:{where})" if where else ""

    if opt.kind in ("int", "float"):
        try:
            value = int(text) if opt.kind == "int" else float(text)
        except ValueError:
            raise ConfigError(
                f'值 "{raw}" 不是{"整数" if opt.kind == "int" else "数字"}。'
                f"期望:{expect};例如 {opt.name}="
                f'{opt.default if opt.default != "" else 1}{suffix}') from None
        if opt.lo is not None and value < opt.lo:
            raise ConfigError(f"值 {value} 小于下限 {opt.lo}(期望:{expect}){suffix}")
        if opt.hi is not None and value > opt.hi:
            raise ConfigError(f"值 {value} 大于上限 {opt.hi}(期望:{expect}){suffix}")
        return value

    if opt.kind == "choice":
        value = text.lower()
        if value not in opt.choices:
            raise ConfigError(
                f'值 "{raw}" 不在允许范围内。可选:{" / ".join(opt.choices)}{suffix}')
        return value

    if opt.kind == "csv":
        return [s.strip() for s in text.split(",") if s.strip()]

    return raw                                  # str:保留原样(尾部空格可能有意义,如口令)


def _opt(name: str) -> Opt:
    return SPEC_BY_NAME.get(name) or Opt(name, "str", "")


def get(name: str) -> Any:
    """读取并按声明解析。未设置或空串 → 返回默认值(空串视为未设置)。

    为什么把空串当未设置:`.env` 里 `FOXCLAW_PORT=` 这种写法今天会让
    `int("")` 抛 ValueError 崩掉,而用户的本意显然是"用默认值"。
    """
    opt = _opt(name)
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return opt.default
    return _parse(opt, raw, _caller())


def get_int(name: str) -> int:
    return int(get(name))


def get_float(name: str) -> float:
    return float(get(name))


_warned: set[str] = set()
_log = logging.getLogger("foxclaw.config")


def get_safe(name: str, default: Any = None) -> Any:
    """可降级的读取:值非法时**退默认值并要求日志警告一次**,不抛异常。

    给"配置错不该拖垮主流程"的地方用(trace 采样、工具超时、搜索超时):这些点
    原来就 try/except 静默退默认,问题是用户永远不知道自己的设置没生效。改成
    "仍然退默认,但警告一次并说清哪个变量、错在哪"。
    """
    opt = _opt(name)
    fallback = opt.default if default is None else default
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return fallback                          # 未设置/空串:用调用方给的兜底
    try:
        return _parse(opt, raw, _caller())
    except ConfigError as e:
        if name not in _warned:
            _warned.add(name)                    # 只喊一次:tracing 是每请求调用
            _log.warning("[config] %s", e)
        return fallback


def mask(value: str) -> str:
    """密钥脱敏:只留前 2 后 2,中间用等长星号(长度也藏掉,免得泄露口令强度)。"""
    v = str(value or "")
    if not v:
        return "(空)"
    if len(v) <= 4:
        return "*" * len(v)
    return f"{v[:2]}{'*' * 6}{v[-2:]}"


def is_secret(name: str) -> bool:
    opt = SPEC_BY_NAME.get(name)
    if opt is not None and opt.secret:
        return True
    return any(t in name for t in ("KEY", "TOKEN", "SECRET", "PASSWORD", "SCODE"))


@lru_cache(maxsize=1)
def known_keys() -> frozenset[str]:
    """包内源码**实际读取**的环境变量名(静态扫 AST,排除 vendor/)。

    比手写清单可靠:新增 `os.environ.get("X")` 就自动算"已知",不需要谁记得来同步。
    只在 `check()` / `show()` 这类诊断路径调用,不进请求路径;结果缓存在本进程内。
    """
    pkg = Path(__file__).resolve().parent.parent
    # vendor/(HKC/HMR)在仓库根,不在包内:它们自己读 HMR_*/HKC_* 变量,不一起扫就会把
    # 正常配置报成"拼错的键"。wheel 里没有 vendor/ → 扫描自然为空,不影响安装后的行为。
    roots = [pkg] + ([pkg.parent / "vendor"] if (pkg.parent / "vendor").is_dir() else [])
    found: set[str] = set(SPEC_BY_NAME)
    files: list[Path] = []
    for root in roots:
        files += [x for x in sorted(root.rglob("*.py"))
                  if "__pycache__" not in x.as_posix() and "tests/" not in x.as_posix()]
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:                      # 语法坏掉的文件留着给 CI 报,这里跳过
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = ast.unparse(node.func)
                if fn in ("os.environ.get", "os.getenv", "environ.get", "getenv"):
                    arg = node.args[0] if node.args else None
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        found.add(arg.value)
            elif isinstance(node, ast.Subscript) and ast.unparse(node.value) in (
                    "os.environ", "environ"):
                if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    found.add(node.slice.value)
    return frozenset(found)


def _provider_issues(env: Mapping[str, str]) -> list[Issue]:
    raw = (env.get("LLM_PROVIDER") or env.get("FOXCLAW_PROVIDER") or "").strip().lower()
    if raw and raw in PROVIDER_KEY_ENV:
        need = PROVIDER_KEY_ENV[raw]
        if not (env.get(need) or "").strip():
            return [Issue(
                "error", "provider_key", need,
                f"已指定 LLM_PROVIDER={raw},但这个 provider 需要的 {need} 是空的 → "
                f"启动后不会真的调用模型,只会静默退化成 stub 演示回复",
                f"补上 {need},或把 LLM_PROVIDER 改成已配好 key 的 provider,或删掉 LLM_PROVIDER 让它自动探测")]
    if not any((env.get(k) or "").strip() for k in PROVIDER_KEY_ENV.values()):
        return [Issue(
            "warn", "no_llm", "",
            "没有任何模型 key(ANTHROPIC/OPENAI/DEEPSEEK/GOOGLE/DASHSCOPE/MINIMAX/LLM_API_KEY 都空)"
            " → 以 stub 演示模式运行",
            "本地试用可以忽略;要真实回答,至少配一个 *_API_KEY")]
    return []


def _consistency_issues(env: Mapping[str, str]) -> list[Issue]:
    out: list[Issue] = []
    key_id = (env.get("FOXCLAW_CHANNEL_SIGNING_KEY_ID") or "").strip()
    secret = (env.get("FOXCLAW_CHANNEL_SIGNING_SECRET") or "").strip()
    if key_id and not secret:
        out.append(Issue("warn", "signing_pair", "FOXCLAW_CHANNEL_SIGNING_KEY_ID",
                         "配了 key_id 但没有 FOXCLAW_CHANNEL_SIGNING_SECRET"
                         " → 签名不会启用,key_id 被忽略",
                         "要么补上 SECRET,要么把 KEY_ID 也删掉"))
    if 0 < len(secret) < 16:
        out.append(Issue("warn", "weak_secret", "FOXCLAW_CHANNEL_SIGNING_SECRET",
                         f"密钥只有 {len(secret)} 个字符,HMAC 强度不足",
                         "至少 32 字节随机值,如 `openssl rand -hex 32` 的输出"))
    if (env.get("FOXCLAW_CORS_ORIGINS") or "").strip() == "*" and not (
            env.get("FOXCLAW_API_KEY") or "").strip():
        out.append(Issue("warn", "open_cors", "FOXCLAW_CORS_ORIGINS",
                         "CORS 全开(\"*\")且没设 FOXCLAW_API_KEY → 任意网页都能调本机 API",
                         "本地开发建议改成 http://localhost:5173 这种具体来源;"
                         "确实要跨源就在设 API_KEY 之后再说"))
    if (env.get("FOXCLAW_GATEWAY") or "").strip() == "0" and (
            env.get("FOXCLAW_API_KEY") or "").strip():
        out.append(Issue("warn", "gateway_off", "FOXCLAW_GATEWAY",
                         "对外开了鉴权(配了 API_KEY)却强制关掉网关限流"
                         " → 拿到口令的人可以不限速地打",
                         "去掉 FOXCLAW_GATEWAY=0(网关默认会随 API_KEY 自动开启)"))
    return out


def _unknown_key_issues(env: Mapping[str, str]) -> list[Issue]:
    known = known_keys()
    out: list[Issue] = []
    for name in sorted(env):
        if name in known or not name.startswith(OWNED_PREFIXES):
            continue
        near = get_close_matches(name, sorted(known), n=1, cutoff=0.75)
        out.append(Issue(
            "warn", "unknown_key", name,
            "这个变量没有任何代码读取,设了也不会生效(多半是拼错)",
            f"你是想写 {near[0]} 吗?" if near else "删掉它,或对照 .env.example 检查拼写"))
    return out


def check(env: Mapping[str, str] | None = None) -> list[Issue]:
    """一次汇总全部配置问题(不抛异常、不 fail-fast)。

    `env` 可注入(dict),便于测试与"拿一份 .env 先试探"。
    """
    env = os.environ if env is None else env
    issues: list[Issue] = []
    for opt in SPECS:
        raw = env.get(opt.name)
        if raw is None or raw.strip() == "":
            continue
        try:
            _parse(opt, raw, where="")
        except ConfigError as e:
            code = "choice" if opt.kind == "choice" else (
                "range" if ("下限" in str(e) or "上限" in str(e)) else "type")
            # 密钥不回显原值,免得把半截口令打进日志
            msg = str(e)
            if is_secret(opt.name):
                msg = f"值 {mask(raw)} 不合法(原始值已脱敏)"
            issues.append(Issue("error", code, opt.name, msg,
                                "" if (opt.hint and opt.hint in msg) else opt.hint))
    issues += _provider_issues(env)
    issues += _consistency_issues(env)
    issues += _unknown_key_issues(env)
    return issues


def format_issues(issues: Iterable[Issue], limit: int = 20) -> str:
    items = list(issues)
    if not items:
        return "配置检查通过:没有发现问题。"
    errors = sum(1 for i in items if i.level == "error")
    lines = [f"配置检查:{errors} 个错误 / {len(items) - errors} 个提示"]
    lines += [str(i) for i in items[:limit]]
    if len(items) > limit:
        lines.append(f"... 另有 {len(items) - limit} 条(用 --json 看全部)")
    return "\n".join(lines)


def fatal_issues(issues: Iterable[Issue]) -> list[Issue]:
    """会在启动早期直接崩掉的那类(类型/取值非法)。网关/模型这类语义问题不算。"""
    return [i for i in issues if i.level == "error" and i.code in FATAL_CODES]
