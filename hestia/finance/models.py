"""
Hestia 财务子系统 — 数据模型
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
    """一笔交易 = 一个可计算的资金行为"""
    amount: float                       # 负数=支出,正数=收入
    category: str                       # 分类:subscription/food/transport/salary...
    entity: str                         # 商户/对手方:Netflix、星巴克、公司
    account: str                        # 账户:credit_card_1、debit、alipay
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    tags: list = field(default_factory=list)   # ["recurring","media"]
    note: str = ""
    id: str = field(default_factory=lambda: f"txn_{uuid.uuid4().hex[:10]}")

    @property
    def is_expense(self) -> bool:
        return self.amount < 0

    @property
    def is_recurring(self) -> bool:
        return "recurring" in self.tags or self.category == "subscription"

    @property
    def dt(self) -> datetime:
        return datetime.fromisoformat(self.timestamp)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Budget:
    """某分类的预算状态"""
    category: str
    monthly_limit: float                # 月度上限
    spent: float = 0.0                  # 本期已花(绝对值)
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
    balance: Optional[float] = None     # 账户余额(仅 account)
    recurring_amount: Optional[float] = None  # 周期金额(仅 subscription)
    cycle: Optional[str] = None         # monthly/yearly(仅 subscription)
    last_seen: str = ""
    txn_count: int = 0
    total_amount: float = 0.0           # 累计金额

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FinancePolicyRule:
    """财务规则:在约束下决策的核心。
    与 Harness 的 Policy YAML 同构,但作用于资金行为。
    """
    id: str
    condition: str                      # 人类可读条件描述
    action: Literal["alert", "require_confirmation", "freeze", "veto", "suggest"]
    threshold: Optional[float] = None
    scope: str = "global"               # global 或某 category
    severity: Literal["info", "warn", "critical"] = "warn"
    source: Literal["builtin", "user", "learned"] = "builtin"
    enabled: bool = True
    # 自校准留痕:calibrated_value = 上次校准写下的阈值(用于判断"之后有没有人手动改过";
    # 若 threshold 已偏离它,说明是人的决定,校准就此收手)。calibrated_from = 依据说明,
    # 让阈值可解释 —— 用户看得到"这个 800 是怎么来的",才敢信它。
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
    requires_user_action: bool = False  # 是否需要用户确认/介入

    def to_dict(self) -> dict:
        return asdict(self)
