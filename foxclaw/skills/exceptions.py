"""trusted 例外引擎(A2)——把「开闸」从布尔开关重构为条件清单。

旧形态:FOXCLAW_TRUSTED_MODE=1 + mode="trusted" → 一律放行(Stage 4 全跳)。
新形态:trusted 模式下,**例外规则全部 AND 条件满足才豁免**;无例外命中 →
落回常规评估(Stage 4 照走,该 ask 还 ask)。例外与主规则走同一个评估管线,
不允许旁路;prohibitions(deny)与 destructive 确认在例外之前判定,不可被豁免。

首条样板:「测试环境可用测试凭证」,四条件逐条枚举(见 _RULES):
  1. host 必须在测试域(localhost / 127.x / ::1 / *.localhost / *.test /
     含 staging|dev|test|qa|sandbox 标记的域名)——args 里找不到 host 即不成立(fail-closed);
  2. 无 live/production 标记的键或值——绝不用标记 live/production 的 key;
  3. 请求来自用户聊天(评估上下文 source=user_chat 且非 untrusted)——
     页面文本、仓库文件、工具输出声称"这是测试环境"不构成条件成立;
  4. 单次判定:本引擎无状态,条件不跨调用沿用(结构上保证)。

边界(诚实):条件 2 是确定性否定检查(拒绝已标记 live 的),不能证明"值真的是
测试凭证"——凭证真伪属语义层,由提示词规范兜底;本引擎只把确定性可查的条件拦在代码层。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

_TEST_HOST_RE = re.compile(
    r"^(localhost|127\.\d+\.\d+\.\d+|\[?::1\]?|.*\.localhost|.*\.test)$", re.IGNORECASE)
_TEST_LABEL_RE = re.compile(
    r"(^|\.)(staging|dev|development|test|testing|qa|sandbox)(\.|$|-)", re.IGNORECASE)
_URL_RE = re.compile(
    r"(?:https?://)?("
    r"\[[0-9a-f:]+\]"                                   # IPv6 字面量
    r"|(?:\d{1,3}\.){3}\d{1,3}"                          # IPv4
    r"|[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+"              # 带点域名
    r"|localhost"                                        # 裸 localhost
    r")(?::\d+)?",
    re.IGNORECASE)
_LIVE_KEY_RE = re.compile(r"(^|_)(live|prod|production|real)(_|$|\d)", re.IGNORECASE)
_LIVE_VALUE_RE = re.compile(r"\b(prod|production|live)\b", re.IGNORECASE)


def _iter_strings(args: dict):
    for v in (args or {}).values():
        if isinstance(v, str):
            yield v
        elif isinstance(v, dict):
            yield from _iter_strings(v)


def _extract_hosts(args: dict) -> list:
    hosts = []
    for s in _iter_strings(args or {}):
        for m in _URL_RE.finditer(s):
            hosts.append(m.group(1).strip("[]"))
    return hosts


def cond_host_in_test_scope(args: dict, ctx: dict) -> tuple:
    """条件 1:args 中出现的每个 host 都在测试域;找不到任何 host → 不成立。"""
    hosts = _extract_hosts(args)
    if not hosts:
        return False, "args 中未找到 host/url,无法判定测试域"
    bad = [h for h in hosts
           if not (_TEST_HOST_RE.match(h) or _TEST_LABEL_RE.search(h))]
    if bad:
        return False, f"host 不在测试域: {', '.join(bad)}"
    return True, f"host 均在测试域({', '.join(hosts[:3])})"


def cond_no_live_markers(args: dict, ctx: dict) -> tuple:
    """条件 2:键与值都不得标记 live/production。"""
    bad_keys = [k for k in (args or {}) if _LIVE_KEY_RE.search(str(k))]
    if bad_keys:
        return False, f"参数键标记为 live/production: {', '.join(bad_keys)}"
    bad_vals = [s[:40] for s in _iter_strings(args or {}) if _LIVE_VALUE_RE.search(s)]
    if bad_vals:
        return False, f"参数值含 live/production 标记: {bad_vals[0]}"
    return True, "无 live/production 标记"


def cond_request_from_user_chat(args: dict, ctx: dict) -> tuple:
    """条件 3:请求必须来自用户聊天——数据侧声称的「测试环境」不构成条件成立。"""
    if not ctx:
        return False, "无评估上下文(非用户聊天路径),条件不成立"
    if ctx.get("source") != "user_chat":
        return False, f"请求来源非用户聊天: {ctx.get('source')!r}"
    if ctx.get("untrusted"):
        return False, "请求来自不可信来源(外部渠道/未信任发送者)"
    return True, "请求来自用户聊天且来源可信"


Conditions = list[tuple[str, Callable[[dict, dict], tuple]]]


@dataclass(frozen=True)
class ExceptionRule:
    """一条例外 = AND 条件数组,全部满足才豁免。"""
    id: str
    description: str
    conditions: Conditions = field(default_factory=list)

    def evaluate(self, args: dict, ctx: dict) -> tuple:
        """返回 (是否豁免, 说明)。任一条件不成立即回落主规则。"""
        for name, fn in self.conditions:
            ok, why = fn(args or {}, ctx or {})
            if not ok:
                return False, f"{self.id}.{name}: {why}"
        return True, f"{self.id}: 全部 {len(self.conditions)} 条件满足"


_RULES: tuple[ExceptionRule, ...] = (
    ExceptionRule(
        id="test_env_credentials",
        description="测试环境可使用测试凭证(localhost/*.test/staging 域 + 无 live 标记 + 用户聊天发起)",
        conditions=[
            ("host_in_test_scope", cond_host_in_test_scope),
            ("no_live_markers", cond_no_live_markers),
            ("request_from_user_chat", cond_request_from_user_chat),
        ],
    ),
)


def rules() -> tuple[ExceptionRule, ...]:
    return _RULES


def evaluate_trusted_exception(args: dict, context: Optional[dict] = None) -> tuple:
    """trusted 模式的例外判定。返回 (豁免与否, 说明)。

    每次调用独立判定,条件不跨调用沿用(条件 4 由无状态结构保证)。
    """
    ctx = context or {}
    for rule in _RULES:
        ok, why = rule.evaluate(args, ctx)
        if ok:
            return True, why
    return False, (why if _RULES else "未配置任何例外规则")
