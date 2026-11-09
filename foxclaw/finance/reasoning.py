"""
Foxclaw 财务子系统 — 推理层(财务 Agent 的大脑)
纯算法,不依赖外部服务。三块:预算推理、风险判断、预测。
核心思想:钱 = 状态,不是记录;财务 Agent 是时间连续系统。
"""

from collections import defaultdict
from datetime import datetime, timedelta

from .models import Budget
from typing import Any


class FinanceReasoning:
    """财务推理引擎:把交易流变成可决策的洞察。"""

    # ── 预算推理 ──────────────────────────────────────────
    @staticmethod
    def spend_by_category(transactions: list, period: str | None = None) -> dict:
        """按分类汇总支出(绝对值)。period 形如 '2026-06',None=全部。"""
        out: defaultdict[str, float] = defaultdict(float)
        for t in transactions:
            if not t.is_expense:
                continue
            if period and not t.timestamp.startswith(period):
                continue
            out[t.category] += abs(t.amount)
        return dict(out)

    @staticmethod
    def budget_status(transactions: list, budgets: list, period: str | None = None) -> list:
        """计算每个预算分类的使用状态。"""
        period = period or datetime.now().strftime("%Y-%m")
        spent = FinanceReasoning.spend_by_category(transactions, period)
        result = []
        for b in budgets:
            b.spent = spent.get(b.category, 0.0)
            b.period = period
            result.append(b.to_dict())
        return result

    @staticmethod
    def overspend_forecast(transactions: list, budget: Budget,
                           period: str | None = None, now=None) -> dict:
        """分类超支预测:按当前消费速度,推算月底是否超预算。"""
        now = now or datetime.now()
        period = period or now.strftime("%Y-%m")
        spent = FinanceReasoning.spend_by_category(transactions, period).get(
            budget.category, 0.0)
        # 当前是本月第几天
        day = now.day
        days_in_month = 30  # 简化
        if day <= 0:
            day = 1
        daily_rate = spent / day
        projected = daily_rate * days_in_month
        # 月初外推不可信:day=1 时一笔正常消费就被 ×30 放大成"月底将超支"。
        # 前 7 天样本太少,趋势是噪音 —— 标记 reliable=False,让裁决层(overspend_trend_alert)
        # 这一周内闭嘴。宁可晚一周提醒真超支,也不要天天误报把用户逼到关掉守门人。
        reliable = day >= 7
        return {
            "category": budget.category,
            "spent_so_far": round(spent, 2),
            "daily_rate": round(daily_rate, 2),
            "projected_month_end": round(projected, 2),
            "monthly_limit": budget.monthly_limit,
            "will_overspend": projected > budget.monthly_limit,
            "projected_overage": round(max(0, projected - budget.monthly_limit), 2),
            "reliable": reliable,
            "day_of_month": day,
        }

    # ── 风险判断 ──────────────────────────────────────────
    @staticmethod
    def detect_anomalies(transactions: list, threshold: float = 3.5) -> list:
        """异常消费检测:用 MAD(中位数绝对偏差)而非均值/标准差,
        小样本下抗离群值更稳健(一笔大额不会把自己的判定基线抬高)。
        threshold 是修正 Z 分数阈值(3.5 是常用经验值)。"""
        from statistics import median
        by_cat = defaultdict(list)
        for t in transactions:
            if t.is_expense:
                by_cat[t.category].append(t)
        anomalies = []
        for cat, txns in by_cat.items():
            amounts = [abs(t.amount) for t in txns]
            if len(amounts) < 3:
                continue
            med = median(amounts)
            # MAD = 偏离中位数的中位数
            mad = median([abs(a - med) for a in amounts])
            if mad == 0:
                # 退化:用均值兜底
                mad = (sum(abs(a - med) for a in amounts) / len(amounts)) or 1e-9
            for t in txns:
                # 修正 Z 分数(0.6745 是正态分布常数)
                mz = 0.6745 * (abs(t.amount) - med) / mad
                if mz >= threshold:
                    anomalies.append({
                        "transaction_id": t.id,
                        "entity": t.entity,
                        "amount": t.amount,
                        "category": cat,
                        "z_score": round(mz, 2),
                        "category_median": round(med, 2),
                        # med>0 才显示"倍数"(当前 is_expense 排除非负额已保证 med>0,
                        # 此处显式防御,避免未来改动 is_expense 定义后除零)
                        "reason": (f"{cat} 类异常消费,金额是中位数的 {abs(t.amount)/med:.1f} 倍"
                                   if med > 0 else f"{cat} 类异常消费(金额 {abs(t.amount):.0f})"),
                    })
        return anomalies

    @staticmethod
    def detect_duplicate_charges(transactions: list,
                                 window_hours: int = 48) -> list:
        """重复扣款识别:同商户、同金额、短时间内多次。"""
        dups = []
        seen = defaultdict(list)
        for t in transactions:
            if not t.is_expense:
                continue
            key = (t.entity, round(abs(t.amount), 2))
            seen[key].append(t)
        for (entity, amount), txns in seen.items():
            if len(txns) < 2:
                continue
            txns_sorted = sorted(txns, key=lambda x: x.dt)
            for i in range(1, len(txns_sorted)):
                gap = (txns_sorted[i].dt - txns_sorted[i-1].dt).total_seconds() / 3600
                if gap <= window_hours:
                    dups.append({
                        "entity": entity,
                        "amount": amount,
                        "count": len(txns),
                        "gap_hours": round(gap, 1),
                        "reason": f"{entity} 在 {gap:.0f} 小时内重复扣款 {amount}",
                    })
                    break
        return dups

    @staticmethod
    def detect_subscription_creep(transactions: list) -> dict:
        """订阅膨胀检测:统计周期性订阅总额与数量。

        同一商户按"最近一次扣款"代表其当前月度订阅价(订阅可能涨价,
        取最新而非遍历到的任意一条;也避免同商户多月记录被累加成虚高总额)。
        """
        latest: dict[str, tuple[Any, Any]] = {}   # entity -> (dt, amount)
        for t in transactions:
            if t.is_recurring and t.is_expense:
                prev = latest.get(t.entity)
                if prev is None or t.dt > prev[0]:
                    latest[t.entity] = (t.dt, abs(t.amount))
        subs = {e: amt for e, (_, amt) in latest.items()}
        total = sum(subs.values())
        return {
            "subscription_count": len(subs),
            "monthly_total": round(total, 2),
            "subscriptions": [{"entity": k, "amount": v}
                              for k, v in sorted(subs.items(), key=lambda x: -x[1])],
            "creep_warning": len(subs) >= 5 or total >= 100,
        }

    @staticmethod
    def detect_upcoming_renewals(transactions: list, horizon_days: int = 35,
                                 now=None) -> list:
        """订阅/年费**续订前**提醒:从历史扣款间隔推断周期,预测下次扣款日。

        周期靠**相邻扣款的中位间隔**判定(月/季/年),而非猜:
          · 25~35 天 → 月订   · 85~95 → 季订   · 355~375 → 年订
        只有 ≥2 次扣款才能算间隔;不落在已知档位的(间隔紊乱)不预测——宁可不提醒,
        也不要为"猜错的日期"制造误报(误报会让用户关掉提醒)。

        horizon_days=35 是**检测**窗口(默认放宽,足够覆盖月订下次扣款);**几天前提醒**
        由规则 renewal_reminder 的 threshold 精确控制(裁决层用 days_until<=threshold 过滤)。
        两者分离,否则用户把规则阈值改成 14 想提前两周提醒,却被这里的窗口硬封在 7 天内 ——
        改了没反应(静默失效)。检测放宽、裁决收窄,阈值才真正生效。
        """
        from statistics import median
        now = now or datetime.now()
        by_entity = defaultdict(list)
        for t in transactions:
            if t.is_recurring and t.is_expense:
                by_entity[t.entity].append(t)
        out = []
        for entity, txns in by_entity.items():
            if len(txns) < 2:
                continue                      # 只有一次扣款 → 无法判周期
            ts = sorted(txns, key=lambda x: x.dt)
            gaps = [(ts[i].dt - ts[i - 1].dt).days for i in range(1, len(ts))]
            gaps = [g for g in gaps if g > 0]
            if not gaps:
                continue
            g = median(gaps)
            if   25 <= g <= 35:   cadence, period = "月订", 30
            elif 85 <= g <= 95:   cadence, period = "季订", 90
            elif 355 <= g <= 375: cadence, period = "年订", 365
            else:
                continue                      # 间隔紊乱 → 不预测(不制造误报)
            next_dt = ts[-1].dt + timedelta(days=period)
            days_until = (next_dt - now).days
            if 0 <= days_until <= horizon_days:
                out.append({"entity": entity, "amount": abs(ts[-1].amount),
                            "cadence": cadence, "days_until": days_until,
                            "next_date": next_dt.strftime("%Y-%m-%d")})
        return out

    @staticmethod
    def detect_pending_refunds(transactions: list, wait_days: int = 14,
                               now=None) -> list:
        """退款未到账:标记了 `refund_expected` 的支出,超过 wait_days 仍无等额入账。

        **必须由用户打 tag**:退款在流水里只是一笔入账,系统无法从交易流推断
        "我期待一笔退款"。若靠"支出后没有等额入账"来猜,会对**每一笔正常消费**报警
        —— 那是灾难性误报。所以宁可要求显式标记。
        """
        now = now or datetime.now()
        credits = [t for t in transactions if not t.is_expense]
        claimed = set()                 # 已被某笔退款认领的入账(按 id)
        out = []
        # 先处理较早的支出,让它优先认领入账(FIFO,符合"先欠先还")
        expected = sorted((t for t in transactions
                           if t.is_expense and "refund_expected" in (t.tags or [])),
                          key=lambda x: x.dt)
        for t in expected:
            amt = abs(t.amount)
            # 同商户、等额、在该支出之后、且**尚未被别的退款认领**的入账 → 退款到账
            # 消耗式匹配:一笔入账只抵一笔支出。否则用户标了两笔同额 refund_expected、
            # 只收到一笔退款时,两笔都被误判为已退款 → 第二笔漏报(其实还没到账)。
            match = next((c for c in credits
                          if id(c) not in claimed and c.entity == t.entity
                          and abs(abs(c.amount) - amt) < 0.01 and c.dt >= t.dt), None)
            if match is not None:
                claimed.add(id(match))
                continue
            waiting = (now - t.dt).days
            if waiting >= wait_days:
                out.append({"entity": t.entity, "amount": amt,
                            "days_waiting": waiting, "transaction_id": t.id})
        return out

    # ── 阈值自校准 ────────────────────────────────────────
    # 样本门槛:少于这些就不校准,继续用出厂默认。宁可用"公认的经验值",
    # 也不要用 3 笔数据算出来的、听起来很个性化的垃圾阈值。
    CALIB_MIN_TXNS = 20
    CALIB_MIN_DAYS = 30

    @staticmethod
    def calibrate_thresholds(transactions: list) -> dict:
        """从**用户自己的消费分布**推阈值,取代拍脑袋的经验值。

        只校准有分布依据的**金额类**阈值,且**必须抗离群**——否则一笔房租/买电脑
        就把门槛抬高,让大额检测在最该警觉时反而变迟钝。所以用 median+MAD 而非均值
        (与本文件 detect_anomalies 同源:同一套 MAD 修正 Z 分数):
          · large_purchase_check   = median + 3.5×MAD 换算界
              = 单笔支出**恰好会触发异常检测的那条线**。语义完全对口:"大额确认"和
               "异常否决"用同一把尺量,只是动作不同(confirm vs veto)。它随消费方差
               自适应:平时消费越集中,门槛压得越贴身;偶发大额不会把它顶上去。
          · low_balance_protection = 中位数日支出 × 7("撑一周"缓冲)
              按天聚合再取中位数,同样抗离群。一周缓冲是有含义的锚,500 只是个数字。

        **时间类阈值(48h 重复扣款窗 / 7 天续订提醒 / 14 天退款等待)不校准** ——
        它们不是从消费分布里学得出来的,是语义常数(一周就是一周,退款该多久到账
        取决于商家不取决于你花多少钱)。假装校准它们,只是给猜测套上数学外衣。
        月订阅总额阈值同理:没有原理性的锚,故留给用户改 yaml。

        返回 {rule_id: (建议阈值, 依据说明)};样本不足返回 {}。
        """
        from statistics import median
        expenses = [t for t in transactions if t.is_expense]
        if len(expenses) < FinanceReasoning.CALIB_MIN_TXNS:
            return {}
        dts = [t.dt for t in expenses]
        span_days = (max(dts) - min(dts)).days
        if span_days < FinanceReasoning.CALIB_MIN_DAYS:
            return {}                      # 跨度太短 → 分布不可信

        def _round100(v):
            # 取整到百:阈值是给人看的,"800" 比 "783.42" 可信也好记
            return max(100.0, round(v / 100.0) * 100.0)

        amts = [abs(t.amount) for t in expenses]
        med = median(amts)
        mad = median([abs(a - med) for a in amts])
        if mad == 0:                       # 退化(消费高度一致)→ 用平均绝对偏差兜底
            mad = (sum(abs(a - med) for a in amts) / len(amts)) or 1e-9
        # 3.5/0.6745:把 detect_anomalies 的修正 Z 阈值(3.5)换算回原尺度偏移量,
        # 使 large 阈值 = 恰好触发异常检测的单笔金额。两条规则从此共用一把尺。
        large = min(_round100(med + (3.5 / 0.6745) * mad), 20000.0)

        by_day: defaultdict[Any, float] = defaultdict(float)
        for t in expenses:
            by_day[t.dt.date()] += abs(t.amount)
        daily_med = median(by_day.values())
        low_bal = _round100(daily_med * 7)
        return {
            "large_purchase_check": (
                large, f"单笔支出 median {med:.0f}+3.5×MAD(与异常检测同尺,基于 "
                       f"{len(expenses)} 笔 / {span_days} 天)"),
            "low_balance_protection": (
                low_bal, f"中位数日支出 {daily_med:.0f} × 7 天缓冲(基于 "
                         f"{len(expenses)} 笔 / {span_days} 天)"),
        }

    # ── 预测 ──────────────────────────────────────────────
    @staticmethod
    def burn_rate(transactions: list, days: int = 30) -> dict:
        """烧钱速度:近 N 天的日均净流出。"""
        cutoff = datetime.now() - timedelta(days=days)
        recent = [t for t in transactions if t.dt >= cutoff]
        expense = sum(abs(t.amount) for t in recent if t.is_expense)
        income = sum(t.amount for t in recent if not t.is_expense and t.amount > 0)
        net = income - expense
        return {
            "window_days": days,
            "total_expense": round(expense, 2),
            "total_income": round(income, 2),
            "net_flow": round(net, 2),
            "daily_burn": round(expense / days, 2),
            "daily_net": round(net / days, 2),
        }

    @staticmethod
    def cashflow_projection(transactions: list, current_balance: float,
                            months: int = 3) -> dict:
        """现金流预测:按当前 burn rate 推算未来 N 个月余额走势。"""
        br = FinanceReasoning.burn_rate(transactions, days=30)
        monthly_net = br["daily_net"] * 30
        trajectory = []
        bal = current_balance
        for m in range(1, months + 1):
            bal += monthly_net
            trajectory.append({"month": m, "projected_balance": round(bal, 2)})
        return {
            "current_balance": current_balance,
            "monthly_net": round(monthly_net, 2),
            "trajectory": trajectory,
            "runway_months": (round(current_balance / abs(monthly_net), 1)
                              if monthly_net < 0 else None),
            "trend": "下降" if monthly_net < 0 else "上升",
        }
