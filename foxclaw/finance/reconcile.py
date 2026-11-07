"""
Foxclaw 财务子系统 — 自主对账 (Reconciliation)

借鉴 2026 前沿财务 Agent 的杀手级用例:自主对账 —— 自动匹配系统记录的交易
与外部流水(银行/支付宝账单),解决差异,生成审计日志。

匹配策略(逐级放宽):
  1. 精确匹配:金额 + 商户 + 同日
  2. 模糊匹配:金额同 + 日期 ±tolerance_days + 商户相似
  3. 金额接近匹配:商户同 + 日期接近 + 金额小幅差异(可能手续费)
四类结果:
  matched           对上的
  unmatched_internal 系统有、外部无(可能漏记/重复记)
  unmatched_external 外部有、系统无(可能漏记交易/盗刷)
  amount_mismatch    匹配上但金额不符(可能手续费/汇率/错记)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ExternalRecord:
    """外部流水记录(来自银行/支付宝账单)。"""
    amount: float
    entity: str
    date: str                      # ISO 日期或 YYYY-MM-DD
    account: str = ""
    ref: str = ""                  # 外部流水号

    def to_dict(self) -> dict:
        return {"amount": self.amount, "entity": self.entity,
                "date": self.date, "account": self.account, "ref": self.ref}


def _date_only(ts: str) -> str:
    """从 timestamp 提取日期部分(YYYY-MM-DD)。"""
    return (ts or "")[:10]


def _days_apart(d1: str, d2: str) -> int:
    """两个日期相差天数(解析失败返回大数)。"""
    try:
        a = datetime.fromisoformat(_date_only(d1))
        b = datetime.fromisoformat(_date_only(d2))
        return abs((a - b).days)
    except Exception:
        return 9999


def _entity_similar(a: str, b: str) -> bool:
    """商户名相似(一方包含另一方,或去空格后相等)。"""
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b:
        return False
    return a == b or a in b or b in a


@dataclass
class ReconciliationResult:
    matched: list = field(default_factory=list)
    unmatched_internal: list = field(default_factory=list)
    unmatched_external: list = field(default_factory=list)
    amount_mismatch: list = field(default_factory=list)
    audit_log: list = field(default_factory=list)

    def summary(self) -> dict:
        total = (len(self.matched) + len(self.unmatched_internal)
                 + len(self.unmatched_external) + len(self.amount_mismatch))
        return {
            "total_checked": total,
            "matched": len(self.matched),
            "unmatched_internal": len(self.unmatched_internal),
            "unmatched_external": len(self.unmatched_external),
            "amount_mismatch": len(self.amount_mismatch),
            "match_rate": round(len(self.matched) / total, 3) if total else 0.0,
            "needs_attention": (len(self.unmatched_internal)
                                + len(self.unmatched_external)
                                + len(self.amount_mismatch)),
        }

    def to_dict(self) -> dict:
        return {
            "summary": self.summary(),
            "matched": self.matched,
            "unmatched_internal": self.unmatched_internal,
            "unmatched_external": self.unmatched_external,
            "amount_mismatch": self.amount_mismatch,
            "audit_log": self.audit_log,
        }


class Reconciler:
    """对账器:交叉核对系统交易与外部流水。"""

    def __init__(self, tolerance_days: int = 3, amount_tolerance: float = 0.01):
        self.tolerance_days = tolerance_days
        self.amount_tolerance = amount_tolerance   # 金额绝对差容忍(<=此值视为相等)

    def _amount_eq(self, a: float, b: float) -> bool:
        """金额模糊相等:绝对值在容差范围内 且 收支方向一致(都支出或都收入)。"""
        same_direction = (a >= 0) == (b >= 0)
        return same_direction and abs(abs(a) - abs(b)) <= self.amount_tolerance

    def reconcile(self, transactions: list, external: list) -> ReconciliationResult:
        """对账主流程。transactions=系统交易,external=[ExternalRecord]。"""
        res = ReconciliationResult()
        used_ext = set()      # 已匹配的外部记录索引

        for t in transactions:
            t_date = _date_only(t.timestamp)
            best = None       # (ext_idx, match_type)
            # 1. 精确匹配:金额 + 商户 + 同日
            for j, ext in enumerate(external):
                if j in used_ext:
                    continue
                if (self._amount_eq(t.amount, ext.amount)
                        and _entity_similar(t.entity, ext.entity)
                        and _days_apart(t_date, ext.date) == 0):
                    best = (j, "exact"); break
            # 2. 模糊匹配:金额同 + 日期容忍内 + 商户相似
            if best is None:
                for j, ext in enumerate(external):
                    if j in used_ext:
                        continue
                    if (self._amount_eq(t.amount, ext.amount)
                            and _entity_similar(t.entity, ext.entity)
                            and _days_apart(t_date, ext.date) <= self.tolerance_days):
                        best = (j, "fuzzy_date"); break
            # 3. 金额不符匹配:商户同 + 日期接近 + 同方向 + 金额不等(可能手续费)
            if best is None:
                for j, ext in enumerate(external):
                    if j in used_ext:
                        continue
                    same_direction = (t.amount >= 0) == (ext.amount >= 0)
                    if (_entity_similar(t.entity, ext.entity)
                            and _days_apart(t_date, ext.date) <= self.tolerance_days
                            and same_direction
                            and not self._amount_eq(t.amount, ext.amount)):
                        best = (j, "amount_mismatch"); break

            if best is None:
                # 系统有、外部无
                res.unmatched_internal.append(self._txn_brief(t))
                res.audit_log.append(
                    f"[未匹配·内部] 「{t.entity} ¥{abs(t.amount):.0f}」"
                    f"({t_date}) 在外部流水中无对应,疑漏记或重复记录")
                continue

            j, mtype = best
            used_ext.add(j)
            ext = external[j]
            if mtype == "amount_mismatch":
                diff = abs(t.amount) - abs(ext.amount)
                res.amount_mismatch.append({
                    "internal": self._txn_brief(t), "external": ext.to_dict(),
                    "diff": round(diff, 2)})
                res.audit_log.append(
                    f"[金额不符] 「{t.entity}」系统¥{abs(t.amount):.0f} vs "
                    f"外部¥{abs(ext.amount):.0f},差¥{abs(diff):.0f}(疑手续费/汇率/错记)")
            else:
                res.matched.append({
                    "internal": self._txn_brief(t), "external": ext.to_dict(),
                    "match_type": mtype})
                res.audit_log.append(
                    f"[已匹配·{mtype}] 「{t.entity} ¥{abs(t.amount):.0f}」({t_date})")

        # 剩余未匹配的外部记录:外部有、系统无
        for j, ext in enumerate(external):
            if j not in used_ext:
                res.unmatched_external.append(ext.to_dict())
                res.audit_log.append(
                    f"[未匹配·外部] 外部流水「{ext.entity} ¥{abs(ext.amount):.0f}」"
                    f"({_date_only(ext.date)}) 系统未记录,疑漏记交易或盗刷")
        return res

    @staticmethod
    def _txn_brief(t) -> dict:
        return {"id": t.id, "amount": t.amount, "entity": t.entity,
                "date": _date_only(t.timestamp), "account": t.account}
