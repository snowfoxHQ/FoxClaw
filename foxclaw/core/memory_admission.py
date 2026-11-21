"""记忆写入准入 —— M1 / M2 / M5 / M6 的唯一判定真源（v0.1.7）。

设计约束
────────
1. 判定必须确定性：只用规则与显式信号，不调 LLM。同一句话今天拦明天放，用户无法建立预期。
2. 不破坏存量：所有写入都会得到一个来源标签；严格模式由环境变量开启，默认兼容既有调用。
3. 尽力而为（M6）：判定函数本身不抛异常；调用方失败只记日志、不中断主任务、不刷屏。

条目对应
────────
M1 来源标签：只有「用户亲口说的」才是 [stated]；agent 结论 / 研究产出 / 转述一律 [derived]。
M2 写入校准：可查数据不入、单次提及不入、反向校准、耐久措辞、已入档即已记住、视野测试。
M5 反投毒：按效果判定「让未来会话少诚实 / 少安全 / 少质疑 / 无条件讨好」，委婉变体同样拒。
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from typing import Iterable

STATED = "[stated]"
DERIVED = "[derived]"
VALID_SOURCE_TAGS = (STATED, DERIVED)

STRICT_ENV = "FOXCLAW_MEMORY_SOURCE_STRICT"

# ── M1：来源判定 ────────────────────────────────────────────────────────────

# 出现这些措辞，说明内容来自 agent 推断 / 工具产出 / 转述，不是用户原话。
_AGENT_SIGNALS = (
    "我建议", "我推断", "我判断", "我分析", "根据搜索", "搜索结果显示",
    "文档显示", "测试结果显示", "命令输出", "日志显示", "研究产出",
    "agent:", "工具返回", "系统检测到",
)
# 显式用户信号（调用方传入 context 时使用）。
_USER_SIGNALS = ("user_stated", "asked_to_remember", "user_confirmed")


def classify_source(
    content: str,
    explicit_tag: str | None = None,
    context: dict | None = None,
) -> str:
    """M1：判定一条写入的来源标签。

    保守原则：没有明确的用户信号就记 [derived]。调用方要写 [stated] 必须
    显式传 `explicit_tag=STATED` 或 `context={"user_stated": True}`。
    """
    if explicit_tag in VALID_SOURCE_TAGS:
        return explicit_tag
    ctx = context or {}
    if any(ctx.get(key) for key in _USER_SIGNALS):
        return STATED
    text = str(content or "")
    if any(signal in text for signal in _AGENT_SIGNALS):
        return DERIVED
    return DERIVED


def source_is_strict() -> bool:
    """严格模式：无显式来源声明的写入被拒（默认关，兼容存量调用）。"""
    return (os.environ.get(STRICT_ENV, "") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


# ── M2：写入校准 ────────────────────────────────────────────────────────────

_SEARCHABLE = re.compile(
    r"(?:搜(?:了)?一下|搜索到|搜索结果|查到|查了一下|网页显示|"
    r"文档(?:里|上|中)?(?:写|说|显示)|"
    r"命令输出|测试输出|日志显示|根据公开资料)"
)
_ONE_OFF = re.compile(r"(?:顺便(?:提|说)一下|偶然|偶尔|有一次|今天突然|刚才想到)")
_TASK_LOCAL = re.compile(r"(?:这篇草稿|这个任务|本次任务|这次会话|当前对话|这个文件里)")
_EXPLICIT_REMEMBER = re.compile(r"(?:记住|别忘了|记一下|remember\b|请记)")
_CONFIRMATION = re.compile(r"^(?:对|是的|没错|就这个|确认|行|好|ok)[，。！!\s]*", re.I)
_DURABLE = re.compile(
    r"(?:以后|从今|一直|总是|通常|习惯|偏好|喜欢|不喜欢|在项目|住在|工作在|负责)"
)


def _normalize(text: str) -> str:
    # 中文「换措辞复写」常只差空格；全部空白折叠后再比对。
    return re.sub(r"\s+", "", str(text or "")).casefold()


def calibration_check(
    content: str,
    source_tag: str,
    existing_contents: Iterable[str] = (),
    context: dict | None = None,
) -> tuple[bool, str]:
    """M2：返回 (是否可写, 理由)。纯函数，不抛异常。"""
    text = str(content or "").strip()
    if not text:
        return False, "空内容"

    # 5. 已入档 = 已记住：换路径重述 / 换措辞复写不产生新写入
    norm = _normalize(text)
    if any(norm == _normalize(old) for old in existing_contents):
        return False, "已入档，重复写入跳过"

    ctx = context or {}
    explicit = bool(_EXPLICIT_REMEMBER.search(text)) or bool(
        ctx.get("user_stated") or ctx.get("asked_to_remember")
    )

    # 1. 可查数据不入；但用户确认过的查到内容是 [stated]，可入
    if _SEARCHABLE.search(text) and not _CONFIRMATION.search(text) and not explicit:
        return False, "可查数据不入记忆（用户确认过的除外）"

    # 2. 单次提及不入（稳定世界事实例外：由 context.stable_fact 声明）
    if _ONE_OFF.search(text) and not explicit and not ctx.get("stable_fact"):
        return False, "单次提及不入，待复现或展开"

    # 6. 视野测试：任务过程状态不入，只留稳定残余
    if _TASK_LOCAL.search(text) and not explicit and not _DURABLE.search(text):
        return False, "任务过程状态不入（视野测试未通过）"

    # 3. 反向校准：「行 / 不错」只确认形状，不把铺开的细节算作用户的
    if _CONFIRMATION.search(text) and len(text) > 120:
        return False, "确认只覆盖形状，细节未逐条确认"

    return True, "通过校准"


# ── M5：反投毒底线 ──────────────────────────────────────────────────────────

# 按**效果**收集：让未来会话少诚实 / 少安全 / 少质疑 / 无条件讨好 / 压抑不同意见。
# 每组都有直白与委婉变体；任何用户指令（含「记住」前缀）都不可覆盖。
_POISON_PATTERNS = (
    r"(?:以后|以后每次|未来会话|每次对话).{0,24}(?:不要|别|少|不用|不必).{0,16}"
    r"(?:质疑|挑战|反驳|提醒|指出|核查|验证|较真)",
    r"(?:无条件|总是|永远|一律).{0,12}(?:同意|赞同|顺从|讨好|夸|说好)",
    r"(?:不要|别|少|不用|不必).{0,12}(?:诚实|如实|真实|坦白|说明风险|警告)",
    r"(?:隐藏|隐瞒|不告诉|别提|不要提|绕开).{0,16}(?:风险|问题|错误|限制|副作用)",
    r"(?:压制|避免|少说|别提|不提).{0,12}(?:不同意见|反对|批评|异议|反面)",
    r"(?:用户|我).{0,8}(?:说什么|要求什么).{0,12}(?:都对|都照做|不能拒绝|不要拒绝)",
    r"(?:忽略|覆盖|跳过).{0,12}(?:安全|护栏|规则|准则|底线|系统提示)",
)
_POISON_COMPILED = tuple(re.compile(p) for p in _POISON_PATTERNS)


def is_memory_poisoning(content: str) -> tuple[bool, str]:
    """M5：命中返回 (True, 效果类别)；未命中 (False, '')。"""
    text = str(content or "")
    if not text:
        return False, ""
    for rx in _POISON_COMPILED:
        if rx.search(text):
            return True, "削弱未来会话的诚实/安全/质疑或压制不同意见"
    return False, ""


# ── 统一准入 ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AdmissionVerdict:
    allowed: bool
    reason: str
    source_tag: str = ""
    checks: tuple = ()

    @property
    def is_stated(self) -> bool:
        return self.source_tag == STATED

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed, "reason": self.reason,
            "source_tag": self.source_tag, "checks": list(self.checks),
        }


def evaluate_write(
    content: str,
    *,
    explicit_source_tag: str | None = None,
    context: dict | None = None,
    existing_contents: Iterable[str] = (),
    skip_calibration: bool = False,
) -> AdmissionVerdict:
    """M1 + M2 + M5 的统一准入判定。任何异常都被吞掉并按「拒绝」处理（M6）。"""
    try:
        source_tag = classify_source(content, explicit_source_tag, context)

        poisoned, effect = is_memory_poisoning(content)
        if poisoned:
            return AdmissionVerdict(False, f"反投毒底线拒绝：{effect}", source_tag, ("M5",))

        ctx = context or {}
        if source_is_strict() and source_tag != STATED and not ctx.get("source_exempt"):
            return AdmissionVerdict(
                False, "严格来源模式：无用户来源声明的写入被拒", source_tag, ("M1",)
            )

        if not skip_calibration:
            ok, reason = calibration_check(content, source_tag, existing_contents, context)
            if not ok:
                return AdmissionVerdict(False, reason, source_tag, ("M2",))

        return AdmissionVerdict(True, "准入通过", source_tag, ("M1", "M2", "M5"))
    except Exception as exc:  # M6：判定自身故障也不能让主任务崩
        return AdmissionVerdict(False, f"准入判定降级拒绝：{type(exc).__name__}", DERIVED, ("M6",))


# ── M6：尽力而为日志（限频，避免刷屏） ──────────────────────────────────────

_log_lock = threading.Lock()
_log_counts: dict[str, int] = {}
_LOG_LIMIT = 5


def safe_log(message: str, key: str = "default") -> None:
    """同一 key 最多打 _LOG_LIMIT 次，超出后静默。记忆是后台行为，不刷屏。"""
    with _log_lock:
        count = _log_counts.get(key, 0)
        if count >= _LOG_LIMIT:
            return
        _log_counts[key] = count + 1
    print(f"[Foxclaw][memory] {message}")


def reset_log_counts() -> None:
    """测试用：清掉限频计数。"""
    with _log_lock:
        _log_counts.clear()
