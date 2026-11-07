"""自然语言记账 —— 用户随口报备的一句花费,要能自己识别、记账、汇总。

这是财务应用第一线的功能,输入是「今天买了一件衣服花了 105 块」这种大白话。
测试固定四件事:
  1. 金额/币种/时间/分类识别对(含中文数字、美元/美金/块/块钱);
  2. 过去/今天 → 已发生,明天/下个月 → 计划支出,且计划不污染"本月已花";
  3. 一段话多笔要拆开,时间可继承;
  4. 记账本汇总(按天/按分类/按币种)与汇率折算正确。
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.finance import FinanceRuntime
from foxclaw.finance.nlparse import (cn_to_number, has_expense_signal,
                                         has_payment_verb, parse)
from foxclaw.agents.impl.finance import _should_autorecord
from foxclaw.agents.impl.supervisor import SupervisorAgent

NOW = datetime(2026, 11, 7, 20, 0)


def _rt(tmp_path):
    return FinanceRuntime(policy_path=str(tmp_path / "finance_policy.yaml"),
                          data_path=str(tmp_path / "finance_data.json"))


def _one(text, now=NOW):
    ps = parse(text, now=now)
    assert len(ps) == 1, f"{text!r} 解析出 {len(ps)} 笔"
    return ps[0]


# ── 1. 用户原话 ────────────────────────────────────────────
def test_今天买衣服105块():
    p = _one("今天买了一件衣服花了105块")
    assert (p.amount, p.currency, p.direction) == (105.0, "CNY", "expense")
    assert p.when.date() == NOW.date() and not p.planned
    assert p.category == "clothing"
    assert p.entity == "衣服"


def test_明天买大米45():
    p = _one("明天买一袋大米花了45")
    assert p.amount == 45.0 and p.planned
    assert p.when.date() == datetime(2026, 11, 8).date()
    assert p.category == "food"


def test_后天洗车100():
    p = _one("后天花了100洗车")
    assert p.amount == 100.0 and p.planned
    assert p.category == "transport"
    assert p.when.date() == datetime(2026, 11, 9).date()


# ── 2. 币种 ───────────────────────────────────────────────
def test_货币单位识别():
    assert _one("花了105块").currency == "CNY"
    assert _one("花了105块钱").currency == "CNY"
    assert _one("花了105元").currency == "CNY"
    assert _one("花了105人民币").currency == "CNY"
    assert _one("花了105rmb").currency == "CNY"
    assert _one("花了105美元").currency == "USD"
    assert _one("花了105美金").currency == "USD"
    assert _one("花了$105").currency == "USD"
    assert _one("花了105港币").currency == "HKD"
    assert _one("花了105欧元").currency == "EUR"


def test_符号与前置币种():
    assert _one("¥105").currency == "CNY"
    assert _one("美元105").currency == "USD"


def test_量级后缀():
    assert _one("买了个包1.5k").amount == 1500.0
    assert _one("买了个手机2万").amount == 20000.0


def test_中文数字():
    assert cn_to_number("一百零五") == 105
    assert cn_to_number("两千三") == 2300
    assert cn_to_number("三千五") == 3500
    assert cn_to_number("十五") == 15
    assert cn_to_number("十万") == 100000
    assert _one("买了一百零五块的衣服").amount == 105.0
    assert _one("两千三买了个手机").amount == 2300.0


# ── 3. 时间关键词 ──────────────────────────────────────────
def test_过去时间词():
    assert _one("昨天买了个杯子30").when.date() == datetime(2026, 11, 6).date()
    assert _one("前天买了个杯子30").when.date() == datetime(2026, 11, 5).date()
    assert _one("大前天买了个杯子30").when.date() == datetime(2026, 11, 4).date()
    assert _one("3天前买了个杯子30").when.date() == datetime(2026, 11, 4).date()


def test_未来时间词():
    assert _one("大后天买了个杯子30").when.date() == datetime(2026, 11, 10).date()
    assert _one("3天后买了个杯子30").when.date() == datetime(2026, 11, 10).date()
    assert _one("下个月5号交房租3000").when.date() == datetime(2026, 12, 5).date()
    assert _one("下个月5号交房租3000").planned


def test_上个月与上月():
    p = _one("上个月花了2000")
    assert p.when.strftime("%Y-%m") == "2026-10" and not p.planned
    assert _one("上月5号买衣服300").when.date() == datetime(2026, 10, 5).date()


def test_几月与几号():
    assert _one("9月买衣服105").when.strftime("%Y-%m") == "2026-09"
    # 本月已过的 5 号 → 本月;本月还没到的 28 号 → 上个月
    assert _one("5号买衣服105", now=datetime(2026, 11, 7)).when.strftime("%Y-%m") == "2026-11"
    assert _one("28号买衣服105", now=datetime(2026, 11, 7)).when.strftime("%Y-%m") == "2026-10"


def test_时间词不会被当成金额():
    p = _one("上个月5号买了双鞋300")
    assert p.amount == 300.0
    assert p.when.date() == datetime(2026, 10, 5).date()


# ── 4. 多笔与分类 ──────────────────────────────────────────
def test_一句话多笔拆分():
    ps = parse("今天买衣服105，还买了大米45", now=NOW)
    assert [p.amount for p in ps] == [105.0, 45.0]
    assert {p.category for p in ps} == {"clothing", "food"}


def test_时间可继承():
    ps = parse("今天买衣服105，洗车100", now=NOW)
    assert len(ps) == 2
    assert all(p.when.date() == NOW.date() for p in ps)


def test_单笔带逗号不拆坏():
    p = _one("今天，我买了一件衣服，花了105块")
    assert p.amount == 105.0 and p.category == "clothing"


def test_收入方向():
    p = _one("工资发了12000")
    assert p.direction == "income" and p.amount == 12000.0
    assert p.category == "income"


# ── 5. 不该记账的句子 ──────────────────────────────────────
def test_无金额不解析():
    assert parse("今天几号", now=NOW) == []
    assert parse("本月预算怎么分配", now=NOW) == []
    assert parse("今天买了几件衣服", now=NOW) == []


def test_花费信号闸门():
    assert has_expense_signal("今天买了一件衣服花了105块")
    assert has_expense_signal("洗车100")
    assert not has_expense_signal("今天几号")
    assert not has_expense_signal("3个月计划")
    assert has_payment_verb("我买了105块的衣服")
    assert not has_payment_verb("衣服预算800")


def test_预算句不自动记账():
    assert not _should_autorecord("衣服预算800")
    assert not _should_autorecord("给我定个800的餐饮预算")
    assert _should_autorecord("今天买了一件衣服花了105块")
    assert _should_autorecord("洗车100")


def test_主管把花费报备路由给财务():
    assert SupervisorAgent._is_expense_report("今天买了一件衣服花了105块")
    assert SupervisorAgent._is_expense_report("后天花了100洗车")
    assert not SupervisorAgent._is_expense_report("帮我总结这篇文档")
    assert not SupervisorAgent._is_expense_report("今天几号")


# ── 6. 记账与汇总 ──────────────────────────────────────────
def test_记账回执与账本(tmp_path):
    rt = _rt(tmp_path)
    rec = rt.record_text("今天买了一件衣服花了105块")
    assert len(rec["recorded"]) == 1
    assert "已记账 1 笔" in rec["ack"] and "105.00" in rec["ack"]

    led = rt.ledger("2026-11")
    assert led["count"] == 1 and led["expense"] == 105.0
    assert led["by_category"][0]["category"] == "clothing"
    assert led["planned"]["count"] == 0


def test_计划支出不进本月已花(tmp_path):
    rt = _rt(tmp_path)
    rt.record_text("今天买了一件衣服花了105块")
    rt.record_text("明天买一袋大米花了45")

    led = rt.ledger()
    assert led["expense"] == 105.0               # 45 还没发生
    assert led["planned"]["count"] == 1
    assert led["planned"]["expense"] == 45.0
    assert rt.analyze()["transaction_count"] == 1  # 分析也不能把未来算进来


def test_多币种折成本位币(tmp_path):
    rt = _rt(tmp_path)
    rt.record_text("花了100美元")           # 内置汇率 7.2
    led = rt.ledger()
    assert led["expense"] == 720.0
    usd = [c for c in led["by_currency"] if c["currency"] == "USD"][0]
    assert usd["original_total"] == -100.0 and usd["base_total"] == -720.0
    assert rt.transactions[0].amount_original == -100.0
    assert rt.transactions[0].fx_rate == 7.2


def test_汇率可改(tmp_path):
    rt = _rt(tmp_path)
    rt.fx.set_rate("USD", 7.0)
    rt.record_text("花了100美金")
    assert rt.ledger()["expense"] == 700.0
    assert _rt(tmp_path).fx.rate("USD") == 7.0      # 汇率也落盘


def test_按天与分类汇总(tmp_path):
    rt = _rt(tmp_path)
    rt.record_text("今天买了一件衣服花了105块")
    rt.record_text("今天在星巴克花了32块钱")
    led = rt.ledger()
    assert led["count"] == 2
    day = led["by_day"][0]
    assert day["count"] == 2 and day["expense"] == -137.0
    cats = {c["category"]: c["total"] for c in led["by_category"]}
    assert cats["clothing"] == 105.0 and cats["food"] == 32.0


def test_记账后账本可持久化(tmp_path):
    rt = _rt(tmp_path)
    rt.record_text("今天买了一件衣服花了105块")
    rt.record_text("上个月5号买了双鞋300")
    again = _rt(tmp_path)
    assert len(again.transactions) == 2
    assert again.ledger("2026-10")["expense"] == 300.0
    assert again.ledger("2026-11")["expense"] == 105.0


def test_未配置汇率按1比1并提示(tmp_path):
    rt = _rt(tmp_path)
    rt.fx.rates.pop("JPY", None)          # 模拟"币种认识但没配汇率"
    rec = rt.record_text("花了100日元")
    assert any("汇率" in w for w in rec["recorded"][0]["warnings"])
    assert rt.ledger()["expense"] == 100.0
