"""失败策略表:把「工具失败了」映射成「下一步做什么」。

病根:工具轮的失败此前只有两种归宿——原样回注给模型,或直接丢掉。于是「超时了该重试
一次还是放弃」「没配搜索服务该降级还是换参数」全靠模型临场发挥;而模型最常见的反应是
**换个说法把没做成的事说成做成了**(静默假成功)。这张表把那类决策从模型手里拿走:
错误特征 → 动作,查表即得,和 recover 层「由策略表决定,不猜」同一原则。

设计取舍:
- **按文本特征分类,不按异常类型**。工具跑在守护线程里(见 agents/mixins/tool_exec),
  跨线程、跨工具之后剩下的只有一句回注文本;异常类型在这里已经丢失。
- **只有只读工具才自动重试**。写/发送/支付类动作重试一次 = 可能重复扣款、重复发消息;
  这类一律交人。
- 分类是**启发式**的,因此:匹配不到 → 当作未知失败(不重试、交人),绝不乐观当成功。
"""
from __future__ import annotations

from dataclasses import dataclass

OK = "ok"
TIMEOUT = "timeout"
TRANSIENT = "transient"
NOT_CONFIGURED = "not_configured"
EMPTY = "empty"
INVALID_ARGS = "invalid_args"
DENIED = "denied"
NEEDS_HUMAN = "needs_human"
EXCEPTION = "exception"
UNKNOWN = "unknown"

NONE = "none"
RETRY = "retry"
REPARAM = "reparam"
DEGRADE = "degrade"
HAND_TO_HUMAN = "hand_to_human"
STOP = "stop"

_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (DENIED, ("已被策略禁用", "被权限策略拒绝", "未知工具")),
    (NEEDS_HUMAN, ("需确认的动作", "需要你确认", "请用户确认", "只能由你亲自确认")),
    (INVALID_ARGS, ("入参不合法", "缺少必填", "类型应为")),
    (TIMEOUT, ("超过", "超时", "timed out", "timeout")),
    (NOT_CONFIGURED, ("未配置", "not configured", "未设置", "缺少 api", "缺少 key")),
    (EMPTY, ("未返回结果", "未召回到", "no result", "未命中", "没有找到")),
    (TRANSIENT, ("网络", "连接失败", "connection", "502", "503", "504", "429", "rate limit")),
    (EXCEPTION, ("执行异常",)),
)

_ACTIONS: dict[str, tuple[str, bool, str]] = {
    TIMEOUT: (RETRY, True,
              "这一步超时未完成,已自动重试一次仍有问题。请如实说明这一步没做成,不要编造结果。"),
    TRANSIENT: (RETRY, True,
                "这次失败像是临时的网络/服务问题,已自动重试一次。若仍失败,如实说明当前无法获取。"),
    EXCEPTION: (RETRY, True,
                "工具内部异常,已自动重试一次。若仍失败,如实说明该步骤未能执行。"),
    EMPTY: (REPARAM, False,
            "调用成功但没有拿到内容。可以换一个更具体的查询词或放宽条件再试一次;"
            "仍为空就如实回答「没有查到」,不要用常识补全成确定结论。"),
    INVALID_ARGS: (REPARAM, False,
                   "参数不合法,这一步没有执行。请按工具声明修正参数后再调用;"
                   "不要把失败当成已完成。"),
    NOT_CONFIGURED: (DEGRADE, False,
                     "该能力当前未配置(服务/密钥缺失),不是暂时故障,重试无用。"
                     "请告知用户这项能力暂不可用,不要假装查到了。"),
    NEEDS_HUMAN: (HAND_TO_HUMAN, False,
                  "这是需要用户确认的动作,我没有执行。把要做的操作拟成草稿交用户确认。"),
    DENIED: (STOP, False, "该工具被策略禁止,不要换个名字或换个参数再试。"),
    UNKNOWN: (HAND_TO_HUMAN, False,
              "这一步的结果不可信(无法判定成功还是失败)。不要向用户断言它已完成。"),
    OK: (NONE, False, ""),
}


@dataclass(frozen=True)
class Decision:
    """一次失败判定:类型 → 动作 + 给模型看的提示。"""

    kind: str
    action: str
    retryable: bool
    hint: str

    @property
    def is_ok(self) -> bool:
        return self.kind == OK

    def to_dict(self) -> dict:
        return {"kind": self.kind, "action": self.action,
                "retryable": self.retryable, "hint": self.hint}


_FAILURE_MARKERS = ("执行异常", "入参不合法", "被权限策略拒绝", "缺少必填")


def looks_like_failure(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    return t.startswith("(") or any(m in t for m in _FAILURE_MARKERS)


def classify(text: str) -> str:
    """把回注文本归类:``ok`` / 具体失败类型 / ``unknown``(认不出来,保守交人)。"""
    t = (text or "").strip()
    if not t:
        return UNKNOWN
    if not looks_like_failure(t):
        return OK
    low = t.lower()
    for kind, needles in _RULES:
        if any(n in low for n in needles):
            return kind
    return UNKNOWN


def decide(text: str, tool: str = "", capability: dict | None = None,
           attempt: int = 1, max_attempts: int = 2) -> Decision:
    """查表得到动作。

    ``attempt`` 是**已尝试次数**(第一次调用传 1)。可重试类型在 ``attempt < max_attempts``
    时给 ``retry``;只读工具才真的会被重试 —— 这里只判定,是否执行由调用方决定,
    因为"这个工具能不能安全重复执行"属于能力声明(capability),不是文本语义。
    """
    kind = classify(text)
    action, retryable, hint = _ACTIONS.get(kind, _ACTIONS[UNKNOWN])
    read_only = bool((capability or {}).get("read_only"))
    if action == RETRY and (not read_only or attempt >= max_attempts):
        action = HAND_TO_HUMAN
        if not read_only:
            hint = ("这一步有副作用,重试可能重复执行,因此没有自动重试。"
                    "请如实说明它没有完成。")
    return Decision(kind=kind, action=action, retryable=retryable, hint=hint)


def annotate(text: str, decision: Decision) -> str:
    """把动作提示拼到回注文本后面(仅失败时;成功文本一个字都不改)。

    为什么要拼在文本里而不是另走一路:回注给模型的是**纯文本**,多一条通道意味着
    模型可能只读到其中一条;拼在末尾让"结果"和"该怎么对待这个结果"不可分割。
    """
    if decision.is_ok or not decision.hint:
        return text or ""
    return f"{text or ''}\n[执行策略] {decision.hint}"
