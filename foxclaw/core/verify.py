"""执行后核验:这一步到底做成了没有,以及凭什么这么说。

病根:工具返回一段文本,模型接着把它写成"已完成"。可这段文本**既不是成功证明,
也不是失败证明**——它只是工具说了什么。于是出现了审核报告里记的那类静默假成功:
发送没发出去、写入没落盘,回答里照样写"已搞定"。人只有事后翻记录才发现。

这里给每个工具结果一个**三态结论** + 可展开的证据:
  · ``verified``   —— 拿到了可核对的后置条件(如搜索结果里的来源链接、命中的本地文件);
  · ``unverified`` —— 执行没报错,但**没有可观察的后置条件**(写/发送/支付类),
                      此时模型不许断言"已完成",只能描述"我执行了什么";
  · ``failed``     —— 明确失败/认不出来。

刻意不做的事:
- **不猜**。没有后置条件的工具就是 ``unverified``,不会因为文本里有"成功"二字就转成
  ``verified``——那句"成功"是工具自己写的,不是证据。
- **不解析自然语言断言**。不去从模型答案里抽取"已完成"再逐个核对(那要靠第二次 LLM
  调用,既慢又会编);证据只从**工具真实返回**里抽,绑在步骤上,由 UI 展示给人看。
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from . import failure_policy as _policy

VERIFIED = "verified"
UNVERIFIED = "unverified"
FAILED = "failed"

#: 每条工具结果最多挂几条证据(再多就成了噪声,用户不会看)
MAX_EVIDENCE = 8

_URL_RE = re.compile(r"https?://[^\s<>\"'）)\]}]+")
#: 只保留"真实存在"的路径 —— 正则从文本里认出来的路径九成是误报(URL 片段、示例路径),
#: 只有能在磁盘上 stat 到的才算证据。这不是限制,是"证据"二字的含义。
_WIN_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|*?，。；]+")
_POSIX_PATH_RE = re.compile(r"(?<![\w:])/(?:[\w.\-]+/)+[\w.\-]+")


@dataclass(frozen=True)
class Evidence:
    """一条可核对的凭据:一个链接、一个真实存在的文件、或一次工具的原始命中。"""

    kind: str                     # url / file
    ref: str                      # 链接或绝对路径
    label: str                    # 展示名(域名 / 文件名)
    source: str                   # 哪个工具产出的
    at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "ref": self.ref, "label": self.label,
                "source": self.source, "at": self.at}


@dataclass
class Verification:
    """一次工具调用的核验结论。"""

    status: str
    reason: str = ""
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == VERIFIED

    def to_dict(self) -> dict:
        return {"status": self.status, "reason": self.reason,
                "evidence": [e.to_dict() for e in self.evidence]}


def _label_for_url(url: str) -> str:
    try:
        host = urlparse(url).netloc
        return host[4:] if host.startswith("www.") else host or url[:40]
    except Exception:
        return url[:40]


def _paths_in(text: str) -> list[str]:
    out: list[str] = []
    for m in _WIN_PATH_RE.finditer(text or ""):
        out.append(m.group(0).rstrip(".,;:，。；："))
    for m in _POSIX_PATH_RE.finditer(text or ""):
        out.append(m.group(0).rstrip(".,;:，。；："))
    return out


def extract_evidence(source: str, text: str, limit: int = MAX_EVIDENCE) -> list[Evidence]:
    """从工具返回文本里抽可核对的证据。

    只认两类硬凭据:URL(去重、截断到 8 条)与**磁盘上确实存在**的路径。
    正文摘要不算证据——那是工具的自述,不是可复核的事实。
    """
    seen: set[str] = set()
    out: list[Evidence] = []
    for m in _URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(".,;:!?，。；：！？")
        key = url.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        out.append(Evidence("url", url, _label_for_url(url), source))
        if len(out) >= limit:
            return out
    for p in _paths_in(text):
        if p in seen:
            continue
        try:
            if not os.path.exists(p):
                continue
        except Exception:
            continue
        seen.add(p)
        out.append(Evidence("file", p, os.path.basename(p), source))
        if len(out) >= limit:
            break
    return out


#: 工具专属的后置条件检查。返回 (是否通过, 不通过的原因)。
#: 没登记的工具走通用规则(见 verify),不会被当成"已验证"。
_CHECKERS: dict[str, "callable"] = {
    # 联网搜索:结论必须能指回来源,否则就是"模型自己写的" —— 标未核验而不是验证通过
    "web_search": lambda args, text: (
        (True, "") if _URL_RE.search(text or "")
        else (False, "搜索结果没有可核对的来源链接")),
    # 个人记忆:召回为空是合法结果(确实没记过),非空即有凭据
    "recall_memory": lambda args, text: (
        (True, "") if (text or "").strip().startswith("【召回的相关记忆】")
        else (False, "未返回记忆条目")),
}


def verify(name: str, args: dict | None, text: str,
           capability: dict | None = None) -> Verification:
    """给一次工具调用的结果下结论(不抛异常:核验本身失败时不拖垮工具轮)。"""
    kind = _policy.classify(text)
    if kind not in (_policy.OK, _policy.UNKNOWN):
        return Verification(FAILED, reason=f"失败类型:{kind}")
    if kind == _policy.UNKNOWN:
        return Verification(FAILED, reason="认不出的返回(不当作成功)")

    ev = extract_evidence(name, text)
    checker = _CHECKERS.get(name)
    if checker is not None:
        try:
            passed, why = checker(args or {}, text or "")
        except Exception:
            passed, why = False, "后置条件检查自身出错"
        if not passed:
            return Verification(UNVERIFIED, reason=why, evidence=ev)
        return Verification(VERIFIED, evidence=ev)

    read_only = bool((capability or {}).get("read_only"))
    if read_only:
        # 只读 + 有返回 = 这一步确实发生过(内容是否可信由污点/证据层管)
        return Verification(VERIFIED, evidence=ev)
    # 有副作用但没登记后置条件:不能证明效果已发生 → 未核验(不许声称完成)
    return Verification(UNVERIFIED, reason="该工具未声明可观察的后置条件", evidence=ev)


def note_for_model(v: Verification, read_only: bool = False) -> str:
    """给模型的硬提示。**只对未核验/失败**追加;已验证的成功结果一个字都不改。

    ``read_only`` 决定未核验时要不要训话:只读工具的"没核对到来源"照样记进证据区
    (UI 显示未核验),但不往结果文本里塞提示 —— 否则每条搜索/记忆命中都会多一段
    劝诫,既挤占上下文又改变既有行为。**有副作用**的未核验必须训:那才是"没做成
    却可能被写成做成了"的地方。
    """
    if v.status == VERIFIED:
        return ""
    if v.status == FAILED:
        return f"[核验] 该步骤未成功({v.reason}),不要向用户声称它已完成。"
    if read_only:
        return ""
    return (f"[核验] 该步骤执行了但没有可核对的凭据({v.reason})。"
            f"只描述你执行了什么,不要断言「已完成/已成功」。")
