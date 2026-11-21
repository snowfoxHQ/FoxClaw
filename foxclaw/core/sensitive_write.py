"""高敏感记忆写入的同意闸门 (SensitiveWritePolicy)。

问题
────
`memory_adapter.ingest` 此前对「写什么内容」不做任何判定:凭据、证件号、诊断结论
一旦被抽进长期记忆,就会在之后每次召回里反复出现在上下文中,而且会随 prompt 发往云端
模型。spec 的 `confirm_high_sensitivity` 要求这类内容落库前得到用户显式同意。

判定必须是确定性的
──────────────────
不用 LLM 判定敏感度:写入路径再调一次模型既贵,结果又不可复现,同一句话今天拦明天放,
用户无法建立预期。这里只用正则 + Luhn 校验,命中即命中,可单测。

误判方向是刻意的
────────────────
漏判 = 该拦的不拦,等于没做;误判 = 用户多点一次确认。所以宁可多拦:「我最近焦虑」
也会被拦一次。反过来,财务 Agent 天天谈金额,因此 finance 类**只匹配卡号/账户号**,
绝不匹配普通金额 —— 否则功能一开就无法使用。

三档模式 (FOXCLAW_SENSITIVE_WRITE_MODE)
──────────────────────────────────────
  audit          只记录不拦截。落库照常,事件里标 allowed。
  warn  (默认)   记录 + 进程内告警,仍不落 pending。
  enforce        真正拦截:高敏感内容不进长期记忆,转成待确认项,用户同意后才落库。

为什么默认 warn
────────────────
分类规则在真实流量上跑通之前是待验证的假设,直接 enforce 会把「我最近焦虑」这类
误判都变成确认弹窗。但默认 audit 意味着凭据/证件落库完全无提示 —— 折中为 warn:
行为与 audit 一致(不拦、不落 pending),但每次命中都有进程内告警,宿主能立刻
看到命中长什么样、误判率多少,验证后切 enforce。
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

MODE_ENV = "FOXCLAW_SENSITIVE_WRITE_MODE"
VALID_MODES = ("audit", "warn", "enforce")

PENDING_FILE = "sensitive_pending.json"
MAX_PENDING = 200


def _mask(s: str) -> str:
    """打码后才允许进事件/日志 —— 审计出口本身不能变成泄露点。"""
    s = str(s or "")
    if len(s) <= 6:
        return "*" * len(s)
    return f"{s[:2]}{'*' * (len(s) - 4)}{s[-2:]}"


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        if not ch.isdigit():
            return False
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


@dataclass(frozen=True)
class Hit:
    category: str
    pattern: str
    sample: str

    def to_dict(self) -> dict:
        return {"category": self.category, "pattern": self.pattern, "sample": self.sample}


# 每个模式都是「有上下文锚点」的,不裸匹配长数字 —— 裸匹配会把订单号、时间戳全卷进来。
_RULES: dict[str, tuple[str, ...]] = {
    "credential": (
        r"(?:密码|口令|password|passwd|pwd)\s*[:：=是为]\s*\S{3,}",
        r"(?:api[_-]?key|secret|token|access[_-]?key)\s*[:：=]\s*[A-Za-z0-9_\-]{12,}",
        r"\bsk-[A-Za-z0-9]{16,}",
        r"\bghp_[A-Za-z0-9]{20,}",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    ),
    "identity": (
        r"\b[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b",
        r"(?:身份证|护照|港澳通行证|社保卡|驾驶证)[号编]?\s*[:：]?\s*[A-Za-z0-9]{6,}",
    ),
    "health": (
        r"(?:确诊|诊断为|病历|住院|手术|处方|服药|用药|过敏史|既往史)",
        r"(?:抑郁症|焦虑症|糖尿病|高血压|心脏病|癌|HIV|乙肝)",
    ),
    "finance_detail": (
        r"(?:银行卡|账户|卡号|账号)\s*[:：]?\s*\d{6,}",
        r"(?:CVV|CVC|安全码|有效期)\s*[:：]?\s*\d{3,4}",
        r"\b(?:信用卡|储蓄卡|借记卡)\b",
    ),
    # M4 v0.1.7：未成年人身份信息（绝不入库）
    "minor_identity": (
        r"(?:小学生|初中生|未成年人|未满\s*1[0-7]\s*岁).{0,16}(?:姓名|名字|学校|班级|身份证|学号)",
        r"(?:儿子|女儿|孩子).{0,10}(?:今年|年龄)?\s*\d{1,2}\s*岁.{0,12}(?:学校|班级|姓名|小学|中学|幼儿园)",
    ),
    # M4 v0.1.7：受保护属性（默认不入库，产品层单独同意才放开）
    "protected_attribute": (
        r"(?:宗教信仰|信奉|政治立场|性取向).{0,12}(?:是|为|属于|倾向)",
        r"(?:倾向|属于|信奉|信仰).{0,8}(?:政治立场|性取向|某宗教|佛教|基督教|道教|伊斯兰)",
    ),
}

# 命中即整段省略的类目：绝不写泛化替身（M4 无占位符原则）。
BLOCK_CATEGORIES = (
    "credential", "identity", "minor_identity", "finance_detail",
)
# 健康 / 受保护属性不属于「绝不入库」，仍走三档闸门的待确认流程。

# 「忘记 X」→ 整行删除，派生内容一并清理（M4）。
_FORGET_REQUEST = re.compile(
    r"(?:忘记|忘掉|删掉|清除|别记了|移除|forget)\s*(?:关于|有关|我)?\s*(.+?)"
    r"(?:$|[。！？!?，,；;])"
)

_COMPILED = {cat: [re.compile(p) for p in pats] for cat, pats in _RULES.items()}

# 卡号单独走一次 Luhn:16-19 位裸数字只有校验通过才算,避免误伤订单号/流水号。
_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){15,18}\d\b")


class SensitivityClassifier:
    """确定性判定:返回命中的类别与打码后的样本。无状态,并发安全。"""

    def __init__(self, categories: Iterable[str] | None = None):
        allowed = set(categories) if categories is not None else set(_COMPILED)
        self.categories = tuple(c for c in _COMPILED if c in allowed)

    def classify(self, content: str) -> tuple[Hit, ...]:
        text = str(content or "")
        if not text:
            return ()
        hits: list[Hit] = []
        for cat in self.categories:
            for rx in _COMPILED[cat]:
                m = rx.search(text)
                if m:
                    hits.append(Hit(cat, rx.pattern, _mask(m.group(0))))
                    break
        if "finance_detail" in self.categories:
            for m in _CARD_CANDIDATE.finditer(text):
                digits = re.sub(r"\D", "", m.group(0))
                if len(digits) >= 16 and _luhn_ok(digits):
                    hits.append(Hit("finance_detail", "luhn", _mask(digits)))
                    break
        return tuple(hits)


def scrub_blocked(content: str) -> str:
    """M4 无占位符原则：命中阻断类目的整行直接省略，绝不写泛化替身。

    「在管某健康问题」与具体病名同等不入；只保留可分离的日常部分，例如
    「帮我盯周报——她在休假」只留下值班安排。
    """
    classifier = SensitivityClassifier(BLOCK_CATEGORIES)
    kept: list[str] = []
    for line in str(content or "").splitlines():
        hits = classifier.classify(line)
        card_hit = any(
            len(re.sub(r"\D", "", m.group(0))) >= 16
            and _luhn_ok(re.sub(r"\D", "", m.group(0)))
            for m in _CARD_CANDIDATE.finditer(line)
        )
        if hits or card_hit:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def parse_forget_request(content: str) -> str:
    """解析「忘记 X」请求，返回 X；非忘记请求返回空串。"""
    match = _FORGET_REQUEST.search(str(content or ""))
    return match.group(1).strip() if match else ""


@dataclass
class Decision:
    allowed: bool
    reason: str
    mode: str
    categories: tuple = ()
    hits: tuple = ()
    pending_id: str = ""

    @property
    def is_pending(self) -> bool:
        return bool(self.pending_id)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed, "reason": self.reason, "mode": self.mode,
            "categories": list(self.categories),
            "hits": [h.to_dict() for h in self.hits],
            "pending_id": self.pending_id,
        }


class PendingStore:
    """待确认的高敏感写入。落盘是因为写入可能跨进程/重启发生,内存队列会静默丢内容。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _read(self) -> list:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def _write(self, items: list) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, entry: dict) -> str:
        with self._lock:
            items = self._read()
            pid = uuid.uuid4().hex[:12]
            items.append({"id": pid, **entry})
            self._write(items[-MAX_PENDING:])
            return pid

    def list(self) -> list:
        with self._lock:
            return self._read()

    def pop(self, pid: str) -> dict | None:
        with self._lock:
            items = self._read()
            kept = [it for it in items if it.get("id") != pid]
            found = next((it for it in items if it.get("id") == pid), None)
            if found is not None:
                self._write(kept)
            return found


class SensitiveWritePolicy:
    """判定「这条内容能否直接写入长期记忆」。纯函数式判定 + 挂载式装配(同 shared_write)。"""

    def __init__(self, mode: str | None = None,
                 classifier: SensitivityClassifier | None = None,
                 pending_path: str | Path | None = None):
        self.mode = self._resolve_mode(mode)
        self.classifier = classifier or SensitivityClassifier()
        self.pending = PendingStore(pending_path or _default_pending_path())

    @staticmethod
    def _resolve_mode(mode: str | None = None) -> str:
        """显式入参 > 环境变量 > 默认 warn。非法值一律回落到 warn(保守:告警可见)。"""
        m = (mode or os.environ.get(MODE_ENV, "") or "warn").strip().lower()
        return m if m in VALID_MODES else "warn"

    def evaluate(self, content: str, **entry: Any) -> Decision:
        hits = self.classifier.classify(content)
        if not hits:
            return Decision(True, "无高敏感特征", self.mode)
        cats = tuple(dict.fromkeys(h.category for h in hits))
        if self.mode == "enforce":
            return Decision(False, "高敏感内容需用户同意后才能写入长期记忆",
                            self.mode, cats, hits)
        label = "audit 不拦截" if self.mode == "audit" else "warn 不拦截"
        return Decision(True, f"高敏感特征({label})", self.mode, cats, hits)

    def stash(self, content: str, entry: dict, decision: Decision) -> Decision:
        """enforce 下把被拦的写入转成待确认项,返回带 pending_id 的 Decision。"""
        pid = self.pending.add({**entry, "content": str(content or ""),
                                "categories": list(decision.categories)})
        return Decision(decision.allowed, decision.reason, decision.mode,
                        decision.categories, decision.hits, pid)


def _default_pending_path() -> Path:
    root = os.environ.get("FOXCLAW_DATA_DIR") or "./foxclaw_data"
    return Path(root) / PENDING_FILE


def guard_sensitive_write(backend, agent_id: str | None, content: str,
                          memory_type: str = "concept", **entry: Any) -> Decision | None:
    """记忆落库前的统一闸门。未挂策略时返回 None(向后兼容,与 guard_shared_write 一致)。

    enforce 且命中高敏感 → 返回 allowed=False 的 Decision,调用方**必须**据此跳过落库;
    被拦内容此时已进入待确认队列,不会丢。
    """
    policy = getattr(backend, "sensitive_write_policy", None)
    if policy is None:
        return None

    decision = policy.evaluate(content, **entry)
    spine = getattr(backend, "sensitive_write_spine", None)
    if spine is not None:
        try:
            spine.publish("memory.sensitive", {
                "agent_id": agent_id or "",
                "type": memory_type,
                "allowed": decision.allowed,
                "mode": decision.mode,
                "categories": list(decision.categories),
                "hits": [_mask(h.sample) for h in decision.hits],
            }, source=agent_id or "memory")
        except Exception:
            pass

    if not decision.allowed:
        if policy.mode == "enforce":
            return policy.stash(content, {"agent_id": agent_id or "",
                                          "type": memory_type}, decision)
        if policy.mode == "warn":
            print(f"[Foxclaw] ⚠ 高敏感记忆写入(未拦截): agent={agent_id!r} "
                  f"categories={list(decision.categories)}")
    return decision


def attach(backend, policy: SensitiveWritePolicy | None = None, spine=None):
    """挂到记忆后端。与 shared_write.attach 同因:装配顺序上脊柱晚于 memory_adapter。"""
    backend.sensitive_write_policy = policy or SensitiveWritePolicy()
    backend.sensitive_write_spine = spine
    return backend.sensitive_write_policy
