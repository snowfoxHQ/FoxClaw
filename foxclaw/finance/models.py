"""
Foxclaw 财务子系统 — 数据模型
四类财务记忆:Transaction(交易)、Budget(预算)、Entity(账户/商户/订阅)、Policy(规则)
核心思想:钱 = 可计算的行为轨迹,不只是记录。
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Literal
import uuid


TxnType = Literal["expense", "income", "transfer"]
EntityType = Literal["account", "merchant", "subscription"]


@dataclass
class Transaction:
    """一笔交易 = 一个可计算的资金行为

    金额口径(重要):`amount` 永远是**本位币(CNY)**金额,预算/风控/现金流都按它算。
    原币金额与币种另存 `original_amount` / `currency` / `fx_rate` —— 这样「100 美元」
    不会和「100 元」在求和时混在一起,同时账本里又看得到用户当时说的原话数额。
    """
    amount: float
    category: str
    entity: str
    account: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    tags: list = field(default_factory=list)
    note: str = ""
    id: str = field(default_factory=lambda: f"txn_{uuid.uuid4().hex[:10]}")
    currency: str = "CNY"
    original_amount: Optional[float] = None
    fx_rate: float = 1.0
    description: str = ""
    status: str = "settled"
    source: str = "manual"

    @property
    def is_expense(self) -> bool:
        return self.amount < 0

    @property
    def is_recurring(self) -> bool:
        return "recurring" in self.tags or self.category == "subscription"

    @property
    def is_planned(self) -> bool:
        """未来才发生的钱(「明天交房租」)。绝不能算进"本月已花"。"""
        return self.status == "planned"

    @property
    def amount_original(self) -> float:
        """原币金额。老数据没存原币时按 amount 折算回来。"""
        if self.original_amount is not None:
            return self.original_amount
        return round(self.amount / self.fx_rate, 2) if self.fx_rate else self.amount

    @property
    def dt(self) -> datetime:
        return datetime.fromisoformat(self.timestamp)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["amount_original"] = self.amount_original
        return d


@dataclass
class Budget:
    """某分类的预算状态"""
    category: str
    monthly_limit: float
    spent: float = 0.0
    period: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m"))

    @property
    def remaining(self) -> float:
        return self.monthly_limit - self.spent

    @property
    def usage_ratio(self) -> float:
        if self.monthly_limit <= 0:
            return 0.0
        return self.spent / self.monthly_limit

    @property
    def is_over(self) -> bool:
        return self.spent > self.monthly_limit

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(remaining=self.remaining, usage_ratio=round(self.usage_ratio, 3),
                 is_over=self.is_over)
        return d


@dataclass
class FinancialEntity:
    """账户 / 商户 / 订阅"""
    name: str
    entity_type: EntityType
    balance: Optional[float] = None
    recurring_amount: Optional[float] = None
    cycle: Optional[str] = None
    last_seen: str = ""
    txn_count: int = 0
    total_amount: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FinancePolicyRule:
    """财务规则:在约束下决策的核心。
    与 Harness 的 Policy YAML 同构,但作用于资金行为。
    """
    id: str
    condition: str
    action: Literal["alert", "require_confirmation", "freeze", "veto", "suggest"]
    threshold: Optional[float] = None
    scope: str = "global"
    severity: Literal["info", "warn", "critical"] = "warn"
    source: Literal["builtin", "user", "learned"] = "builtin"
    enabled: bool = True
    calibrated_value: Optional[float] = None
    calibrated_from: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PolicyVerdict:
    """规则裁决结果 — 守门人的判断"""
    rule_id: str
    action: str
    severity: str
    message: str
    transaction_id: Optional[str] = None
    requires_user_action: bool = False

    def to_dict(self) -> dict:
        return asdict(self)
