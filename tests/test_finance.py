"""
B 阶段测试 — 财务 Agent 内核(资金行为操作系统)
验证:CSV 导入 → 推理(预算/异常/订阅/烧钱) → Policy 守门人裁决
不真实动钱:执行层只产出审批请求。
"""
import sys, tempfile
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.finance import FinanceRuntime, Transaction
from foxclaw.finance.reasoning import FinanceReasoning

CSV = str(Path(__file__).parent.parent / "examples" / "sample_transactions.csv")


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


def main():
    print("=" * 60)
    print("B 阶段 · 财务 Agent 内核(资金行为操作系统)")
    print("=" * 60)

    # policy 落临时目录:原先写 ./b_finance_policy.yaml 再 os.remove —— 一旦中途抛异常
    # 就会在仓库根留下垃圾文件。改临时目录后无需清理,也不会污染工作区。
    fr = FinanceRuntime(policy_path=str(Path(tempfile.mkdtemp()) / "b_finance_policy.yaml"))
    n = fr.import_csv(CSV)
    fr.set_budget("food", 400)
    fr.set_budget("subscription", 100)
    fr.set_balance("debit", 11800)
    fr.set_balance("credit_card_1", -700)
    print(f"\n导入 {n} 笔交易")
    check(n == 13, f"CSV 导入 13 笔交易(实为 {n})")

    r = fr.analyze(period="2026-06", current_balance=11100)

    print("\n【预算推理】")
    for b in r["budget_status"]:
        flag = "⚠ 超支" if b["is_over"] else "OK"
        print(f"  {b['category']:<14} {b['spent']:.0f}/{b['monthly_limit']:.0f} "
              f"({b['usage_ratio']*100:.0f}%) {flag}")
    over = {b["category"]: b["is_over"] for b in r["budget_status"]}
    check(over.get("food") is True and over.get("subscription") is True,
          f"两大超支分类被正确标记(food={over.get('food')}, subscription={over.get('subscription')})")

    print("\n【超支预测】")
    for f in r["overspend_forecast"]:
        print(f"  {f['category']:<14} 月底预计 {f['projected_month_end']:.0f} / "
              f"限额 {f['monthly_limit']:.0f} · 将超支={f['will_overspend']}")
    check(len(r["overspend_forecast"]) >= 2
          and all(f["will_overspend"] for f in r["overspend_forecast"]),
          f"超支预测命中({len(r['overspend_forecast'])} 项,全部 will_overspend=True)")

    print("\n【异常消费(MAD 检测)】")
    for a in r["anomalies"]:
        print(f"  {a['entity']} {a['amount']} (修正Z={a['z_score']}) — {a['reason']}")
    check(any(a["entity"] == "高级餐厅" for a in r["anomalies"]),
          f"MAD 检测出「高级餐厅」离群消费({len(r['anomalies'])} 条异常)")

    print("\n【重复扣款】")
    for d in r["duplicate_charges"]:
        print(f"  {d['reason']}")
    if not r["duplicate_charges"]:
        print("  无")
    check(isinstance(r["duplicate_charges"], list), "重复扣款检测返回列表结构")

    print("\n【订阅膨胀】")
    sc = r["subscription_creep"]
    print(f"  {sc['subscription_count']} 个订阅,月总 {sc['monthly_total']:.0f},"
          f"预警={sc['creep_warning']}")
    check(sc["subscription_count"] == 6 and sc["creep_warning"] is True,
          f"订阅膨胀预警触发(6 个订阅 / 月总 {sc['monthly_total']:.0f})")

    print("\n【烧钱速度 / 现金流】")
    br = r["burn_rate"]
    print(f"  日均支出 {br['daily_burn']} · 日均净流 {br['daily_net']}")
    cf = r["cashflow_projection"]
    if cf:
        print(f"  现金流趋势: {cf['trend']} · 月净流 {cf['monthly_net']}")
        for t in cf["trajectory"]:
            print(f"    第 {t['month']} 月预计余额 {t['projected_balance']}")
        check(len(cf["trajectory"]) == 3, "现金流预测给出 3 个月轨迹")

    # ⚠ burn_rate 是**相对 now 的 30 天窗口**,而 examples/sample_transactions.csv 的交易
    # 日期固定在 2026-06 —— 随真实时间推移,样例必然滑出窗口,于是上面那段会**静默变成 0
    # 却毫无报错**(本测试此前不断言,故退化无人发现)。这里做两件事:
    #   (a) 用"当下日期"的交易构造可控用例,把 burn/cashflow 的算术真正锁住;
    #   (b) 断言其与 now-30天 窗口严格一致,把这个耦合显式写出来(而非假装它是稳定值)。
    _cutoff = datetime.now() - timedelta(days=30)
    in_window = [t for t in fr.transactions if t.dt >= _cutoff]
    expect_expense = round(sum(abs(t.amount) for t in in_window if t.is_expense), 2)
    check(br["total_expense"] == expect_expense,
          f"burn_rate 严格按 now-30天 窗口统计(窗口内 {len(in_window)} 笔;样例日期已过期,故为 {expect_expense})")
    check(isinstance(br["daily_burn"], (int, float)) and br["daily_burn"] == round(expect_expense / 30, 2),
          f"日均支出 = 窗口内支出 / 30(daily_burn={br['daily_burn']})")

    fresh = [
        Transaction(amount=-300, category="food", entity="测试超市", account="debit"),
        Transaction(amount=-150, category="transport", entity="测试打车", account="debit"),
        Transaction(amount=2000, category="salary", entity="某公司", account="debit"),
    ]
    br2 = FinanceReasoning.burn_rate(fresh, days=30)
    check(br2["total_expense"] == 450.0 and br2["total_income"] == 2000.0,
          f"当日交易用例:支出 450 / 收入 2000(实为 {br2['total_expense']}/{br2['total_income']})")
    check(br2["daily_burn"] == 15.0, f"日均支出 = 450/30 = 15.0(实为 {br2['daily_burn']})")
    check(br2["daily_net"] == round(1550 / 30, 2),
          f"日均净流 = (2000-450)/30(实为 {br2['daily_net']})")
    cf2 = FinanceReasoning.cashflow_projection(fresh, current_balance=1000.0, months=3)
    check(cf2["trend"] == "上升" and cf2["monthly_net"] > 0, "净流入 → 趋势判定为上升")
    check(cf2["trajectory"][-1]["projected_balance"] > 1000.0, "预测余额随净流入增长")

    print("\n【守门人裁决(整体状态)】")
    for v in r["gatekeeper_verdicts"]:
        print(f"  [{v['action'].upper()}] {v['message']}")
    acts = {v["action"].lower() for v in r["gatekeeper_verdicts"]}
    check("veto" in acts and "alert" in acts,
          f"整体裁决含 VETO(异常消费否决)与 ALERT(订阅超标)(实为 {sorted(acts)})")

    print("\n【守门人裁决(单笔大额交易)】")
    big = Transaction(amount=-680, category="electronics", entity="Apple Store",
                      account="credit_card_1")
    big_acts = {v.action.lower() for v in fr.policy.evaluate_transaction(big, account_balance=300)}
    for v in fr.policy.evaluate_transaction(big, account_balance=300):
        print(f"  [{v.action.upper()}] {v.message}")
    check("require_confirmation" in big_acts,
          "大额支出(680>500)触发 REQUIRE_CONFIRMATION")
    check("freeze" in big_acts,
          "余额不足(300<500)触发 FREEZE 可选支出")

    print("\n【执行层:审批流(不自动动钱)】")
    req = fr.request_action("cancel_subscription", {"entity": "YouTube", "save": 12.0})
    print(f"  请求: {req['action']} · 状态: {req['status']}")
    print(f"  说明: {req['note']}")
    # 本文件 docstring 的承诺是"不真实动钱":执行层必须只产出**待批**状态。
    # 若哪天有人把它改成自动执行(EXECUTED/DONE),这里必须红。
    check("PENDING" in str(req["status"]).upper(),
          f"执行层只产出待批状态,不自动动钱(status={req['status']})")

    print("\n" + "=" * 60)
    print("✓ B 阶段验证完成:财务内核(记忆+推理+Policy 守门人)成立")
    print("=" * 60)


def test_finance():
    main()


if __name__ == "__main__":
    main()
