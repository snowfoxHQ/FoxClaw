"""
B 阶段测试 — 财务 Agent 内核(资金行为操作系统)
验证:CSV 导入 → 推理(预算/异常/订阅/烧钱) → Policy 守门人裁决
不真实动钱:执行层只产出审批请求。
"""
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from hestia.finance import FinanceRuntime, Transaction

CSV = str(Path(__file__).parent.parent / "examples" / "sample_transactions.csv")


def main():
    print("=" * 60)
    print("B 阶段 · 财务 Agent 内核(资金行为操作系统)")
    print("=" * 60)

    fr = FinanceRuntime(policy_path="./b_finance_policy.yaml")
    n = fr.import_csv(CSV)
    fr.set_budget("food", 400)
    fr.set_budget("subscription", 100)
    fr.set_balance("debit", 11800)
    fr.set_balance("credit_card_1", -700)
    print(f"\n导入 {n} 笔交易")

    r = fr.analyze(period="2026-06", current_balance=11100)

    print("\n【预算推理】")
    for b in r["budget_status"]:
        flag = "⚠ 超支" if b["is_over"] else "OK"
        print(f"  {b['category']:<14} {b['spent']:.0f}/{b['monthly_limit']:.0f} "
              f"({b['usage_ratio']*100:.0f}%) {flag}")

    print("\n【超支预测】")
    for f in r["overspend_forecast"]:
        print(f"  {f['category']:<14} 月底预计 {f['projected_month_end']:.0f} / "
              f"限额 {f['monthly_limit']:.0f} · 将超支={f['will_overspend']}")

    print("\n【异常消费(MAD 检测)】")
    for a in r["anomalies"]:
        print(f"  {a['entity']} {a['amount']} (修正Z={a['z_score']}) — {a['reason']}")

    print("\n【重复扣款】")
    for d in r["duplicate_charges"]:
        print(f"  {d['reason']}")
    if not r["duplicate_charges"]:
        print("  无")

    print("\n【订阅膨胀】")
    sc = r["subscription_creep"]
    print(f"  {sc['subscription_count']} 个订阅,月总 {sc['monthly_total']:.0f},"
          f"预警={sc['creep_warning']}")

    print("\n【烧钱速度 / 现金流】")
    br = r["burn_rate"]
    print(f"  日均支出 {br['daily_burn']} · 日均净流 {br['daily_net']}")
    cf = r["cashflow_projection"]
    if cf:
        print(f"  现金流趋势: {cf['trend']} · 月净流 {cf['monthly_net']}")
        for t in cf["trajectory"]:
            print(f"    第 {t['month']} 月预计余额 {t['projected_balance']}")

    print("\n【守门人裁决(整体状态)】")
    for v in r["gatekeeper_verdicts"]:
        print(f"  [{v['action'].upper()}] {v['message']}")

    print("\n【守门人裁决(单笔大额交易)】")
    big = Transaction(amount=-680, category="electronics", entity="Apple Store",
                      account="credit_card_1")
    for v in fr.policy.evaluate_transaction(big, account_balance=300):
        print(f"  [{v.action.upper()}] {v.message}")

    print("\n【执行层:审批流(不自动动钱)】")
    req = fr.request_action("cancel_subscription", {"entity": "YouTube", "save": 12.0})
    print(f"  请求: {req['action']} · 状态: {req['status']}")
    print(f"  说明: {req['note']}")

    os.remove("./b_finance_policy.yaml") if os.path.exists("./b_finance_policy.yaml") else None

    print("\n" + "=" * 60)
    print("✓ B 阶段验证完成:财务内核(记忆+推理+Policy 守门人)成立")
    print("=" * 60)


if __name__ == "__main__":
    main()
