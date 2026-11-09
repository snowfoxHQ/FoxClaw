"""领域推理评测框架 —— 让"推理深度"从感觉变成**数字**。

为什么需要它:守门人的价值不取决于"规则多不多",而取决于
  · **漏报**(该抓没抓)→ 规则缺 → 补规则
  · **误报**(拦了但没事)→ 规则太粗/阈值不合 → **必须能定位是哪条规则干的**,才改得动
误报率一高,用户就会把守门人关掉 —— 那它就形同虚设。所以误报是一等公民指标。

怎么用(给未来的你/我):
  · 往 CASES 里加**你真实遇到的情形** + 期望裁决,跑一次就知道漏报/误报和**归因**。
  · 加一条新规则后重跑:立刻看到它抓住了什么、误伤了什么。
  · 与 test_evals_smoke.py(治理层拦截率/误报)同款方法,搬到领域推理层。

裁决语义:approve=放行 / confirm=要你确认 / reject=拒。
"标注"= expect_flag:True 表示"这笔该被拦下来(confirm 或 reject)"。
"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_domain_evals():
    from foxclaw.finance import FinanceRuntime
    from foxclaw.finance.models import Transaction
    from foxclaw.finance.reasoner import FinanceGatekeeperReasoner

    print("=" * 60); print("领域推理评测(财务守门人)"); print("=" * 60)


    def _policy():
        return FinanceRuntime(policy_path=tempfile.mktemp(suffix=".yaml"),
                              data_path=tempfile.mktemp(suffix=".json")).policy


    CASES = [
        ("买咖啡",        -35,   "food",     8000, False),
        ("午饭",          -60,   "food",     8000, False),
        ("打车",          -80,   "transport", 8000, False),
        ("买菜",          -150,  "food",     8000, False),
        ("买书",          -220,  "book",     8000, False),
        ("聚餐 AA",       -320,  "food",     8000, False),
        ("买鞋",          -450,  "shopping", 8000, False),
        ("买手机",        -4500, "digital",  8000, True),
        ("交房租",        -3000, "housing",  8000, True),
        ("买笔记本",      -8000, "digital",  20000, True),
        ("低余额时消费",  -100,  "food",     300,  True),
        ("发工资",        +15000, "salary",  8000, False),
    ]


    def evaluate(cases, policy):
        """跑评测,返回 (漏报, 误报) —— 误报带**归因**(是哪条规则拦的)。"""
        misses, false_pos = [], []
        for name, amount, category, balance, expect_flag in cases:
            txn = Transaction(amount=amount, category=category, entity=name, account="default")
            rv = FinanceGatekeeperReasoner(policy).reason((txn, balance))
            flagged = rv.decision in ("confirm", "reject")
            if expect_flag and not flagged:
                misses.append((name, amount, rv.decision))
            elif not expect_flag and flagged:
                rules = [e.factor for e in rv.evidence if e.raw is not None]
                false_pos.append((name, amount, rv.decision, rules))
        return misses, false_pos


    pol = _policy()
    misses, fps = evaluate(CASES, pol)
    n = len(CASES)
    n_pos = sum(1 for c in CASES if c[4])
    n_neg = n - n_pos
    miss_rate = len(misses) / n_pos if n_pos else 0.0
    fp_rate = len(fps) / n_neg if n_neg else 0.0

    print(f"\n案例 {n}(该拦 {n_pos} / 不该拦 {n_neg})")
    print(f"  漏报率 = {miss_rate:.0%}  ({len(misses)}/{n_pos})")
    print(f"  误报率 = {fp_rate:.0%}  ({len(fps)}/{n_neg})")
    if misses:
        print("  【漏报】该抓没抓 → 缺规则:")
        for nm, amt, d in misses:
            print(f"      {nm}({amt}) → {d}")
    if fps:
        print("  【误报】拦了但没事 → 归因到规则(据此改阈值/规则):")
        for nm, amt, d, rules in fps:
            print(f"      {nm}({amt}) → {d}  ← 触发规则: {rules or '(未归因)'}")

    assert not misses, f"存在漏报(该抓没抓): {misses}"
    print("\n[1] 无漏报(>500 与低余额保护都抓住了)OK")

    assert fp_rate <= 0.15, f"误报率过高({fp_rate:.0%}),用户会关掉守门人: {fps}"
    print(f"[2] 误报率 {fp_rate:.0%} ≤ 15%(沿用 evals_smoke 同款红线)OK")

    pol_bad = _policy()
    for r in pol_bad.rules:
        if r.id == "large_purchase_check":
            r.threshold = 100.0
    _m2, fps2 = evaluate(CASES, pol_bad)
    assert fps2, "阈值压到 100 应制造出误报(用于验证归因)"
    assert all("large_purchase_check" in rules for _, _, _, rules in fps2), \
        f"误报必须能归因到具体规则,否则无从改起: {fps2}"
    print(f"[3] 误报可归因(阈值压到100 → {len(fps2)} 条误报,全部指向 large_purchase_check)OK")

    from foxclaw.finance.policy import DEFAULT_RULES
    p1, p2 = _policy(), _policy()
    r1 = [r for r in p1.rules if r.id == "large_purchase_check"][0]
    r2 = [r for r in p2.rules if r.id == "large_purchase_check"][0]
    rd = [r for r in DEFAULT_RULES if r.id == "large_purchase_check"][0]
    assert r1 is not r2, "不同 runtime 不得共享同一规则对象"
    r1.threshold = 99.0
    assert r2.threshold != 99.0, "改一个 runtime 的阈值污染了另一个(共享可变状态)"
    assert rd.threshold != 99.0, "改 runtime 的阈值污染了模块级 DEFAULT_RULES"
    print("[4] 规则实例隔离(改一处不污染其它 runtime / 模块级默认)OK")

    assert rd.threshold == 500.0, f"默认单笔阈值应为 500,实际 {rd.threshold}"
    print("[5] 阈值为可调项(finance_policy.yaml 可改,默认 500)OK")

    from datetime import datetime, timedelta
    from foxclaw.finance.models import Budget
    from foxclaw.finance.reasoning import FinanceReasoning as FR

    def _txn(amount, entity, days_ago=0, category="misc", tags=None):
        return Transaction(amount=amount, category=category, entity=entity, account="default",
                           timestamp=(datetime.now() - timedelta(days=days_ago)).isoformat(),
                           tags=tags or [])

    def _fire(**kw):
        """跑状态裁决,返回触发的 rule_id 集合。"""
        return {v.rule_id for v in _policy().evaluate_state(**kw)}

    dups = FR.detect_duplicate_charges([
        _txn(-99, "SomeApp", days_ago=1), _txn(-99, "SomeApp", days_ago=1),
    ])
    assert dups, "重复扣款应被检测到"
    assert "duplicate_charge_alert" in _fire(duplicates=dups), "检测到却不裁决(老毛病)"
    assert "duplicate_charge_alert" not in _fire(duplicates=[]), "无重复扣款不该报警"
    print(f"[6] 同一商家高频扣款 → 触发裁决({dups[0]['reason']})OK")

    f_trend = {"category": "food", "spent_so_far": 800, "monthly_limit": 1000,
               "projected_month_end": 1600, "projected_overage": 600, "will_overspend": True}
    f_safe = {"category": "food", "spent_so_far": 200, "monthly_limit": 1000,
              "projected_month_end": 400, "projected_overage": 0, "will_overspend": False}
    assert "overspend_trend_alert" in _fire(forecasts=[f_trend]), "超支趋势应提醒"
    assert "overspend_trend_alert" not in _fire(forecasts=[f_safe]), "不会超不该提醒"
    print("[7] 跨月超支趋势(还没超但会超 → 提醒;不会超 → 不吵)OK")

    annual = [_txn(-598, "某会员", days_ago=362 + 365, tags=["recurring"]),
              _txn(-598, "某会员", days_ago=362, tags=["recurring"])]
    ren = FR.detect_upcoming_renewals(annual, horizon_days=7)
    assert ren and ren[0]["cadence"] == "年订" and ren[0]["days_until"] <= 7, ren
    assert "renewal_reminder" in _fire(renewals=ren)
    messy = [_txn(-50, "乱扣", days_ago=200, tags=["recurring"]),
             _txn(-50, "乱扣", days_ago=3, tags=["recurring"])]
    assert FR.detect_upcoming_renewals(messy) == [], "间隔紊乱不该预测续订日"
    assert FR.detect_upcoming_renewals([_txn(-50, "新订阅", days_ago=1, tags=["recurring"])]) == []
    print(f"[8] 年费续订前提醒({ren[0]['entity']} {ren[0]['days_until']} 天后;紊乱/单次不预测)OK")

    overdue = FR.detect_pending_refunds([_txn(-300, "某电商", days_ago=20, tags=["refund_expected"])])
    assert overdue and overdue[0]["days_waiting"] >= 14, overdue
    assert "refund_overdue_alert" in _fire(pending_refunds=overdue)
    done = FR.detect_pending_refunds([_txn(-300, "某电商", days_ago=20, tags=["refund_expected"]),
                                      _txn(+300, "某电商", days_ago=2)])
    assert done == [], "退款已到账不该再报"
    assert FR.detect_pending_refunds([_txn(-300, "某电商", days_ago=20)]) == [], \
        "未标记的普通消费不得触发退款报警(灾难性误报)"
    print("[9] 退款未到账(需标记;已到账不报;未标记的普通消费不误报)OK")

    assert _fire() == set(), f"无信号时不该有任何裁决: {_fire()}"
    print("[10] 无信号 → 零裁决(新规则不制造背景噪音)OK")

    def _stream(each, n=60, entity="日常"):
        """造 n 笔支出,跨**正好** n-1 天(满足校准的样本门槛)。

        基准时刻只取一次:若每笔各调 datetime.now(),60 次调用间的微秒漂移会把跨度
        啃掉不到一天,而 .days 向下取整 → span 变成 58,校准值随机器快慢漂移。
        """
        base = datetime.now()
        return [Transaction(amount=-each, category="misc", entity=entity, account="default",
                            timestamp=(base - timedelta(days=i)).isoformat())
                for i in range(n)]

    assert FR.calibrate_thresholds([]) == {}, "空数据不该校准"
    assert FR.calibrate_thresholds(_stream(300, n=5)) == {}, "5 笔就校准 = 用垃圾数据冒充个性化"
    assert FR.calibrate_thresholds(_stream(300, n=30)) == {}, \
        "笔数够但跨度只有 29 天(差一天到门槛)→ 日均不可信,不该校准"
    print("[11] 样本不足(空/笔数少/跨度短)→ 不校准,沿用出厂默认 OK")

    _PAT = [30, 45, 60, 80, 120, 55, 90, 40, 150, 70]
    def _real(scale=1.0, n=80, spike=None):
        base = datetime.now()
        txns = [Transaction(amount=-_PAT[i % len(_PAT)] * scale, category="misc",
                            entity="日常", account="default",
                            timestamp=(base - timedelta(days=i)).isoformat())
                for i in range(n)]
        if spike:
            txns.append(Transaction(amount=-spike, category="misc", entity="大额",
                        account="default", timestamp=(base - timedelta(days=1)).isoformat()))
        return txns

    rich = FR.calibrate_thresholds(_real(scale=2.0))
    poor = FR.calibrate_thresholds(_real(scale=0.5))
    lr, lp = rich["large_purchase_check"][0], poor["large_purchase_check"][0]
    assert lr > lp, f"高消费者的大额门槛应更高: 富{lr} vs 穷{lp}"
    assert rich["low_balance_protection"][0] > poor["low_balance_protection"][0]
    print(f"[12] 阈值随消费水平伸缩(富→大额{lr:.0f} / 穷→大额{lp:.0f};固定 500 对两者都错)OK")

    base_large = FR.calibrate_thresholds(_real())["large_purchase_check"][0]
    spiked_large = FR.calibrate_thresholds(_real(spike=8000))["large_purchase_check"][0]
    assert abs(spiked_large - base_large) <= 100, \
        f"一笔大额离群把门槛从 {base_large} 顶到 {spiked_large}(均值锚的老毛病,MAD 应免疫)"
    print(f"[12b] 抗离群:加一笔 8000 大额,门槛 {base_large:.0f}→{spiked_large:.0f} 几乎不动 OK")

    assert set(rich) == {"large_purchase_check", "low_balance_protection"}, \
        f"只有金额类阈值有分布依据;时间类不得假装校准: {set(rich)}"
    print("[13] 时间类阈值(48h/7d/14d)不参与校准(无分布依据,不给猜测套数学外衣)OK")

    cal = FR.calibrate_thresholds(_real())
    cal_large = cal["large_purchase_check"][0]
    p14 = _policy()
    ch = p14.apply_calibration(cal)
    r14 = [r for r in p14.rules if r.id == "large_purchase_check"][0]
    assert r14.threshold == cal_large and r14.source == "learned" and r14.calibrated_value == cal_large
    assert "median" in (r14.calibrated_from or ""), "阈值必须可解释,用户才敢信"
    assert "large_purchase_check" in {c["rule_id"] for c in ch}, ch
    assert p14.apply_calibration(cal) == [], "值没变还写盘 = 每次 analyze 都刷一遍 yaml"
    print(f"[14] 校准落盘 + 可解释({r14.calibrated_from})+ 幂等 OK")

    p_user = _policy()
    for r in p_user.rules:
        if r.id == "large_purchase_check": r.source = "user"
    p_user.apply_calibration(rich)
    assert [r for r in p_user.rules if r.id == "large_purchase_check"][0].threshold == 500.0, \
        "source=user 是显式锁定,校准不得覆盖"

    p_edit = _policy()
    for r in p_edit.rules:
        if r.id == "large_purchase_check": r.threshold = 1234.0
    p_edit.apply_calibration(rich)
    assert [r for r in p_edit.rules if r.id == "large_purchase_check"][0].threshold == 1234.0, \
        "阈值偏离出厂值 = 有人有意改过,校准必须收手"

    p_re = _policy()
    p_re.apply_calibration(rich)
    for r in p_re.rules:
        if r.id == "large_purchase_check": r.threshold = 777.0
    p_re.apply_calibration(poor)
    assert [r for r in p_re.rules if r.id == "large_purchase_check"][0].threshold == 777.0, \
        "校准值被人改过 = 人的决定,不得再次覆盖"
    print("[15] 校准不踩人的决定(user 锁定 / 手改出厂值 / 手改校准值 → 三种都收手)OK")

    assert rd.threshold == 500.0, f"校准污染了模块级 DEFAULT_RULES(阈值变成 {rd.threshold})"
    from foxclaw.finance.policy import FACTORY_THRESHOLDS
    assert FACTORY_THRESHOLDS["large_purchase_check"] == 500.0, "出厂快照被污染 → 判定基准失效"
    print("[16] 校准不污染模块级出厂默认/出厂快照 OK")

    rt = FinanceRuntime(policy_path=tempfile.mktemp(suffix=".yaml"),
                        data_path=tempfile.mktemp(suffix=".json"))
    for t in _real():
        rt.add_transaction(t, run_gatekeeper=False)
    rep = rt.analyze()
    assert rep["threshold_calibration"], "analyze 必须把阈值变更报出来"
    assert any(c["rule_id"] == "large_purchase_check" and c["from"] == 500.0
               and c["to"] == cal_large for c in rep["threshold_calibration"]), \
        rep["threshold_calibration"]
    assert rt.analyze()["threshold_calibration"] == [], "已校准后重复 analyze 不该再报变更"
    print(f"[17] 阈值变更对用户可见(analyze 报出 500→{cal_large:.0f}),且不重复打扰 OK")


    from foxclaw.finance.models import Transaction as _T
    def _jul(day, amt):
        return _T(amount=-amt, category="food", entity="午饭", account="default",
                  timestamp=f"2026-07-{day:02d}T12:00:00")
    food_budget = Budget(category="food", monthly_limit=1000)
    early = FR.overspend_forecast([_jul(1, 100)], food_budget,
                                  now=datetime(2026, 7, 2))
    assert early["will_overspend"] and not early["reliable"], early
    assert "overspend_trend_alert" not in _fire(forecasts=[early]), "月初噪音不得报警(A 修复)"
    late = FR.overspend_forecast([_jul(d, 100) for d in range(1, 16)], food_budget,
                                 now=datetime(2026, 7, 20))
    assert late["reliable"], late
    print("[18]【A】月初(day<7)超支外推 reliable=False → 不误报;7 天后才裁决 OK")

    monthly_sub = [_txn(-30, "月刊会员", days_ago=10 + 30, tags=["recurring"]),
                   _txn(-30, "月刊会员", days_ago=10, tags=["recurring"])]
    ren = FR.detect_upcoming_renewals(monthly_sub)
    assert ren and ren[0]["cadence"] == "月订" and 18 <= ren[0]["days_until"] <= 22, ren
    assert "renewal_reminder" not in _fire(renewals=ren), "默认提前 7 天 → 20 天后的续订还太远,不报"
    p19 = _policy()
    for r in p19.rules:
        if r.id == "renewal_reminder": r.threshold = 25.0
    v19 = {x.rule_id for x in p19.evaluate_state(renewals=ren)}
    assert "renewal_reminder" in v19, "阈值改到 25 应生效(旧代码被检测层硬封 7 天 → 静默失效)"
    print("[19]【C】续订阈值真正生效(默认7天不报20天后的;改25天则报)OK")

    two_expected = [
        _txn(-300, "某电商", days_ago=40, tags=["refund_expected"]),
        _txn(-300, "某电商", days_ago=30, tags=["refund_expected"]),
        _txn(+300, "某电商", days_ago=5),
    ]
    pend = FR.detect_pending_refunds(two_expected)
    assert len(pend) == 1, f"两笔标记只退一笔 → 应恰好报 1 笔未到账,实得 {len(pend)}: {pend}"
    both_back = two_expected[:2] + [_txn(+300, "某电商", days_ago=5),
                                    _txn(+300, "某电商", days_ago=4)]
    assert FR.detect_pending_refunds(both_back) == [], "两笔退款都到账不该报"
    print("[20]【D】退款消耗式匹配(两笔同额退一笔→报第二笔;都退→不报)OK")

    import threading as _th
    from foxclaw.finance.policy import FinancePolicyEngine
    rt21 = FinanceRuntime(policy_path=tempfile.mktemp(suffix=".yaml"),
                          data_path=tempfile.mktemp(suffix=".json"))
    for t in _real():
        rt21.add_transaction(t, run_gatekeeper=False)
    errs21, seen_to = [], []
    _lk = _th.Lock()
    def _hammer():
        try:
            rep = rt21.analyze()
            with _lk:
                seen_to.append(tuple(sorted((c["rule_id"], c["to"])
                                            for c in rep["threshold_calibration"])))
        except Exception as e:
            with _lk: errs21.append(repr(e))
    _ts = [_th.Thread(target=_hammer) for _ in range(16)]
    for t in _ts: t.start()
    for t in _ts: t.join()
    assert not errs21, f"并发 analyze/校准抛异常: {errs21[:2]}"
    assert sum(1 for s in seen_to if s) == 1, \
        f"首次校准应只发生一次(其余幂等空),实得 {sum(1 for s in seen_to if s)} 次非空"
    reload21 = FinancePolicyEngine(str(rt21.policy.policy_path))
    r21 = [r for r in reload21.rules if r.id == "large_purchase_check"][0]
    assert r21.threshold == cal_large and r21.source == "learned", \
        f"并发后 yaml 落盘损坏或值不对: threshold={r21.threshold}"
    print(f"[21]【E】16 线程并发校准:无异常 · 首次只一次 · yaml 完好(large={r21.threshold:.0f})OK")

    print("\n领域推理评测通过")

if __name__ == "__main__":
    test_domain_evals()
