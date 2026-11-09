"""
Foxclaw 财务子系统 — Policy 引擎(守门人)
核心思想:AI 不是自由决策,而是在规则约束下决策。
财务 Agent 是唯一拥有"强约束执行权"的 Agent:veto / freeze / require_confirmation。
规则用 YAML 持久化,与 Harness 的 Policy 模式同构。
"""

import os
import copy
import threading
import yaml
from pathlib import Path
from typing import Optional

from .models import FinancePolicyRule, PolicyVerdict, Transaction


# 内置默认规则(对应用户设计里的示例规则)
DEFAULT_RULES = [
    FinancePolicyRule(
        id="subscription_limit",
        condition="月度订阅总额 > 100",
        action="alert",
        threshold=100.0,
        scope="subscription",
        severity="warn",
        source="builtin",
    ),
    FinancePolicyRule(
        # 阈值 500(原 200):200 会让几乎每次日常消费都要确认 → 误报爆炸 → 用户索性
        # 把守门人关掉,那它就形同虚设。守门人的价值不在"规则多",而在**误报低到你愿意一直开着**。
        # 这是可调项:按自己的消费水平改 finance_policy.yaml 即可,不用动代码。
        id="large_purchase_check",
        condition="单笔支出 > 500",
        action="require_confirmation",
        threshold=500.0,
        scope="global",
        severity="warn",
        source="builtin",
    ),
    FinancePolicyRule(
        id="low_balance_protection",
        condition="账户余额 < 500",
        action="freeze",
        threshold=500.0,
        scope="global",
        severity="critical",
        source="builtin",
    ),
    # ── 用户点名要的 4 条(2026-07-16)────────────────────────────────
    # 前两条的**检测早就实现了**(detect_duplicate_charges / overspend_forecast),
    # 但此前只进分析报告、从不产生裁决 —— 检测到了却不吭声。现接成规则。
    FinancePolicyRule(
        id="duplicate_charge_alert",
        condition="同商户短时间内重复扣款 ≥2 次",
        action="require_confirmation",
        threshold=2.0,              # 次数阈值
        scope="merchant",
        severity="warn",
        source="builtin",
    ),
    FinancePolicyRule(
        id="overspend_trend_alert",
        condition="按当前速度预测月底将超预算(尚未超)",
        action="alert",
        threshold=0.0,
        scope="budget",
        severity="warn",
        source="builtin",
    ),
    FinancePolicyRule(
        id="renewal_reminder",
        condition="订阅/年费将在 7 天内自动续订",
        action="alert",
        threshold=7.0,              # 提前几天提醒
        scope="subscription",
        severity="info",
        source="builtin",
    ),
    FinancePolicyRule(
        # 注:必须由用户给交易打 tag `refund_expected` —— 系统无法从交易流推断
        # "我期待一笔退款"(退款只是一笔入账)。不标记则此规则恒不触发。
        id="refund_overdue_alert",
        condition="标记了 refund_expected 的支出,14 天内未收到等额退款",
        action="alert",
        threshold=14.0,             # 等待天数
        scope="merchant",
        severity="warn",
        source="builtin",
    ),
    FinancePolicyRule(
        id="anomaly_veto",
        condition="检测到异常消费(Z>3)",
        action="veto",
        threshold=3.0,
        scope="global",
        severity="critical",
        source="builtin",
    ),
]


# 出厂阈值快照(模块加载时取,此后 DEFAULT_RULES 不再被改 —— 见 _load_or_init 的 deepcopy)。
# 自校准靠它判断某条规则"是否还停在出厂状态"。
FACTORY_THRESHOLDS = {r.id: r.threshold for r in DEFAULT_RULES}


class FinancePolicyEngine:
    """财务守门人:对单笔交易 / 整体状态做裁决。"""

    def __init__(self, policy_path: str = "./finance_policy.yaml"):
        self.policy_path = Path(policy_path).resolve()
        self.rules: list = []
        # 线程安全铁律:finance_runtime 是单例(Foxclaw.__init__),analyze()→apply_calibration
        # 会被请求线程并发调用,改 rule.threshold 并 save() 写 yaml(非原子)。写侧必须串行,
        # 否则并发 save 可能把 yaml 写坏。RLock:apply_calibration 持锁时还要调 save()。
        # 读侧(evaluate_*)只读单个 float,GIL 下原子(读到旧值或新值都合法),不加锁省热路径开销。
        self._write_lock = threading.RLock()
        self._load_or_init()

    def _load_or_init(self):
        if self.policy_path.exists():
            self._load()
        else:
            # deepcopy 而非 list():list() 只复制**列表**、不复制里面的 FinancePolicyRule 对象,
            # 于是所有实例共享同一批可变规则 —— 任何一处改阈值(rule.threshold=...)会污染
            # 其它 runtime **以及模块级 DEFAULT_RULES**(要重启才恢复)。规则是可变对象,必须深拷。
            self.rules = copy.deepcopy(DEFAULT_RULES)
            self.save()

    def _load(self):
        try:
            data = yaml.safe_load(self.policy_path.read_text(encoding="utf-8")) or {}
            self.rules = [FinancePolicyRule(**r) for r in data.get("rules", [])]
            if not self.rules:
                self.rules = copy.deepcopy(DEFAULT_RULES)
        except Exception:
            # 同 _load_or_init:必须 deepcopy。这两个兜底分支是上次漏改的 ——
            # 自校准会写 rule.threshold,一旦走到这里就会污染模块级 DEFAULT_RULES。
            self.rules = copy.deepcopy(DEFAULT_RULES)

    def save(self):
        with self._write_lock:
            os.makedirs(self.policy_path.parent, exist_ok=True)
            data = {
                "version": 1,
                "domain": "finance",
                "rules": [r.to_dict() for r in self.rules],
            }
            # 写临时文件再原子 rename:避免并发/崩溃时读到写了一半的 yaml
            tmp = self.policy_path.with_suffix(self.policy_path.suffix + ".tmp")
            tmp.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            os.replace(tmp, self.policy_path)

    def add_rule(self, rule: FinancePolicyRule):
        with self._write_lock:
            self.rules = [r for r in self.rules if r.id != rule.id]
            self.rules.append(rule)
            self.save()

    # ── 阈值自校准 ────────────────────────────────────────
    def _may_calibrate(self, rule: FinancePolicyRule) -> bool:
        """这条规则的阈值,自动校准能不能动?

        铁律:**校准只动没人碰过的阈值**。守门人的阈值是用户的意志表达,
        系统不能因为"我算出个更好的数"就把人的决定覆盖掉 —— 那会让用户
        发现自己改的设置莫名其妙变回去,从此不再信任这个系统。
          · source=user    → 显式锁定,永不动
          · source=builtin → 仅当阈值仍等于**出厂值**(偏离 = 有人有意改过 → 收手)
          · source=learned → 仅当阈值仍等于**我上次校准写下的值**(被改过 = 人的决定 → 收手)
        """
        if rule.source == "user":
            return False
        if rule.source == "builtin":
            return rule.threshold == FACTORY_THRESHOLDS.get(rule.id)
        if rule.source == "learned":
            return (rule.calibrated_value is not None
                    and rule.threshold == rule.calibrated_value)
        return False

    def apply_calibration(self, suggestions: dict) -> list:
        """把 reasoning 算出的建议阈值写回规则。返回实际改动的说明列表。

        分工照旧:**reasoning 算,本引擎裁决/落盘**。
        整体持写锁:check(_may_calibrate)→act(改 threshold)→save 必须原子,
        否则两个请求线程并发首次校准会交错改规则、并发写 yaml。
        """
        with self._write_lock:
            changed = []
            for rule in self.rules:
                s = suggestions.get(rule.id)
                if not s or not self._may_calibrate(rule):
                    continue
                new_val, basis = s
                if new_val == rule.threshold:
                    continue                   # 值没变 → 不写盘(analyze 每次都会调到这里)
                changed.append({"rule_id": rule.id, "from": rule.threshold,
                                "to": new_val, "basis": basis})
                rule.threshold = new_val
                rule.calibrated_value = new_val   # 留痕:此后有人改动就能认出来
                rule.calibrated_from = basis
                rule.source = "learned"
            if changed:
                self.save()
            return changed

    # ── 守门人裁决 ────────────────────────────────────────
    def evaluate_transaction(self, txn: Transaction,
                             account_balance: Optional[float] = None) -> list:
        """对单笔交易做规则裁决,返回所有触发的 verdict。"""
        verdicts = []
        for rule in self.rules:
            if not rule.enabled:
                continue
            v = self._check_rule(rule, txn, account_balance)
            if v:
                verdicts.append(v)
        return verdicts

    def _check_rule(self, rule: FinancePolicyRule, txn: Transaction,
                    balance: Optional[float]) -> Optional[PolicyVerdict]:
        amt = abs(txn.amount)

        if rule.id == "large_purchase_check" and txn.is_expense:
            if amt > (rule.threshold or 0):
                return PolicyVerdict(
                    rule_id=rule.id, action=rule.action, severity=rule.severity,
                    message=f"大额支出 {amt}(>{rule.threshold}),需要确认",
                    transaction_id=txn.id, requires_user_action=True,
                )

        if rule.id == "low_balance_protection" and balance is not None:
            if balance < (rule.threshold or 0):
                return PolicyVerdict(
                    rule_id=rule.id, action=rule.action, severity=rule.severity,
                    message=f"账户余额 {balance}(<{rule.threshold}),冻结可选支出",
                    transaction_id=txn.id, requires_user_action=True,
                )

        return None

    def evaluate_state(self, subscription_total: float = 0.0,
                       anomalies: list | None = None, duplicates: list | None = None,
                       forecasts: list | None = None, renewals: list | None = None,
                       pending_refunds: list | None = None) -> list:
        """对整体财务状态做裁决。

        分工:**runtime 算派生信号,本引擎只裁决**(沿用既有纹理)。
        duplicates/forecasts 的检测逻辑早就存在(detect_duplicate_charges / overspend_forecast),
        但此前只进分析报告、**从不产生裁决** —— 即"检测到了却不吭声"。现已接成规则。
        """
        verdicts = []
        anomalies = anomalies or []
        duplicates = duplicates or []
        forecasts = forecasts or []
        renewals = renewals or []
        pending_refunds = pending_refunds or []
        for rule in self.rules:
            if not rule.enabled:
                continue

            # 同一商家高频/重复扣款(悄悄连扣)
            if rule.id == "duplicate_charge_alert":
                for d in duplicates:
                    if d.get("count", 0) >= (rule.threshold or 2):
                        verdicts.append(PolicyVerdict(
                            rule_id=rule.id, action=rule.action, severity=rule.severity,
                            message=f"疑似重复扣款:{d.get('reason', '')}",
                            requires_user_action=True))

            # 跨月预算超支**趋势**(还没超,但按当前速度月底会超)
            if rule.id == "overspend_trend_alert":
                for f in forecasts:
                    # reliable 闸:月初 7 天内样本太少,外推是噪音,不报(见 overspend_forecast)
                    if (f.get("reliable", True) and f.get("will_overspend")
                            and f.get("spent_so_far", 0) <= f.get("monthly_limit", 0)):
                        verdicts.append(PolicyVerdict(
                            rule_id=rule.id, action=rule.action, severity=rule.severity,
                            message=(f"{f['category']} 按当前速度月底将超预算 "
                                     f"{f.get('projected_overage', 0):.0f}"
                                     f"(预计 {f.get('projected_month_end', 0):.0f} / "
                                     f"限额 {f.get('monthly_limit', 0):.0f})"),
                            requires_user_action=False))

            # 订阅/年费自动续订**前**提醒
            if rule.id == "renewal_reminder":
                for r in renewals:
                    if r.get("days_until", 999) <= (rule.threshold or 7):
                        verdicts.append(PolicyVerdict(
                            rule_id=rule.id, action=rule.action, severity=rule.severity,
                            message=(f"{r['entity']} 将在 {r['days_until']} 天后自动续订 "
                                     f"{r.get('amount', 0):.0f}({r.get('cadence', '')})"),
                            requires_user_action=True))

            # 退款未到账(需用户显式标记 refund_expected —— 见 detect_pending_refunds)
            if rule.id == "refund_overdue_alert":
                for p in pending_refunds:
                    if p.get("days_waiting", 0) >= (rule.threshold or 14):
                        verdicts.append(PolicyVerdict(
                            rule_id=rule.id, action=rule.action, severity=rule.severity,
                            message=(f"{p['entity']} 的退款 {p.get('amount', 0):.0f} "
                                     f"已等待 {p['days_waiting']} 天未到账"),
                            requires_user_action=True))
            if rule.id == "subscription_limit" and subscription_total > (rule.threshold or 0):
                verdicts.append(PolicyVerdict(
                    rule_id=rule.id, action=rule.action, severity=rule.severity,
                    message=f"月度订阅总额 {subscription_total:.0f}(>{rule.threshold}),建议精简",
                    requires_user_action=False,
                ))
            if rule.id == "anomaly_veto":
                for a in anomalies:
                    if a.get("z_score", 0) >= (rule.threshold or 3):
                        verdicts.append(PolicyVerdict(
                            rule_id=rule.id, action=rule.action, severity=rule.severity,
                            message=f"异常消费否决:{a.get('reason','')}",
                            transaction_id=a.get("transaction_id"),
                            requires_user_action=True,
                        ))
        return verdicts

    def summary(self) -> dict:
        return {
            "total_rules": len(self.rules),
            "enabled": sum(1 for r in self.rules if r.enabled),
            "rules": [r.to_dict() for r in self.rules],
        }
