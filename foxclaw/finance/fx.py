"""本位币折算 —— 多币种记账的汇率表。

为什么需要它:用户会说「花了 105 块」也会说「花了 100 美元」。如果两者都写进
`Transaction.amount` 再求和,100 美元会被当成 100 元 —— 预算、风控、现金流预测
会一起错,而且错得看不出来。所以账本统一用**本位币(CNY)记账**,原币金额与币种
另存,汇总时两种都能看。

汇率是**静态参考价,不联网**:宁可给一个用户能改的明确数字,也不假装实时行情。
用户可在 `finance_fx.json` 或 `/api/finance/fx` 里改成自己的记账汇率(比如当天
刷卡的实际结算价,那才是真实成本)。
"""

import json
from pathlib import Path

BASE_CURRENCY = "CNY"

# 参考价(1 单位外币 = 多少 CNY)。宁可粗,但要能改。
DEFAULT_RATES = {
    "CNY": 1.0,
    "USD": 7.2,
    "HKD": 0.92,
    "EUR": 7.8,
    "JPY": 0.048,
    "GBP": 9.1,
    "KRW": 0.0052,
    "SGD": 5.3,
    "AUD": 4.7,
    "CAD": 5.2,
}


class FxTable:
    """币种 → 本位币折算率,带落盘(原子写)。"""

    def __init__(self, path: str | None = None):
        self.path = Path(path) if path else None
        self.rates = dict(DEFAULT_RATES)
        self.note = "static"
        self._load()

    def _load(self):
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            rates = data.get("rates") if isinstance(data, dict) else None
            if isinstance(rates, dict):
                for k, v in rates.items():
                    try:
                        self.rates[str(k).upper()] = float(v)
                    except (TypeError, ValueError):
                        continue
                if data.get("note"):
                    self.note = data["note"]
        except Exception:
            # 汇率表坏了不能拖垮记账:退回出厂参考价,下次保存时自动覆盖
            pass

    def save(self) -> bool:
        if not self.path:
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"base": BASE_CURRENCY, "note": self.note,
                                       "rates": self.rates},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
            return True
        except Exception:
            return False

    def rate(self, currency: str) -> float:
        """取折算率。未知币种返回 1.0,由调用方标注「按 1:1 暂记」。"""
        if not currency:
            return 1.0
        return float(self.rates.get(str(currency).upper(), 1.0))

    def known(self, currency: str) -> bool:
        return str(currency or "").upper() in self.rates

    def to_base(self, amount: float, currency: str = BASE_CURRENCY) -> tuple:
        """原币 → (本位币金额, 折算率)。"""
        r = self.rate(currency)
        return round(float(amount) * r, 2), r

    def set_rate(self, currency: str, rate: float) -> dict:
        cur = str(currency or "").upper().strip()
        if not cur or rate is None or float(rate) <= 0:
            raise ValueError("币种与汇率都必须有效(汇率 > 0)")
        self.rates[cur] = round(float(rate), 6)
        self.note = "user"
        self.save()
        return self.snapshot()

    def snapshot(self) -> dict:
        return {"base": BASE_CURRENCY, "note": self.note,
                "rates": dict(sorted(self.rates.items())),
                "path": str(self.path) if self.path else None}
