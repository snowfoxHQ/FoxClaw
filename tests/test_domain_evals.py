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
        # 用产品**默认**规则集评测 —— 评的就是"开箱默认够不够好"
        # data_path 必须独立:默认落在 policy 同目录,而 mktemp 的父目录是**系统 Temp**,
        # 不显式隔离就会和别的测试共用一个 finance_data.json(顺序一变就互相污染)。
        return FinanceRuntime(policy_path=tempfile.mktemp(suffix=".yaml"),
                              data_path=tempfile.mktemp(suffix=".json")).policy


    # ── 标注案例集:(说明, 金额, 类别, 余额, 该不该被拦) ───────────────────
    # ↓↓↓ 往这里加你真实遇到的情形 ↓↓↓
    CASES = [
        # 日常小额:不该被拦(这是误报的主要来源——原 200 阈值下它们全会被拦)
        ("买咖啡",        -35,   "food",     8000, False),
        ("午饭",          -60,   "food",     8000, False),
        ("打车",          -80,   "transport", 8000, False),
        ("买菜",          -150,  "food",     8000, False),
        ("买书",          -220,  "book",     8000, False),
        ("聚餐 AA",       -320,  "food",     8000, False),
        ("买鞋",          -450,  "shopping", 8000, False),
        # 大额:该被拦(>500)
        ("买手机",        -4500, "digital",  8000, True),
        ("交房租",        -3000, "housing",  8000, True),
        ("买笔记本",      -8000, "digital",  20000, True),
        # 余额保护:低余额该被拦(即便金额不大)
        ("低余额时消费",  -100,  "food",     300,  True),
        # 收入:不该被拦
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
                # 归因:证据里 raw 非空的就是**触发裁决的规则**(informational 证据 raw=None)
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

    # [1] 漏报:该抓的必须都抓住(>500 / 低余额)
    assert not misses, f"存在漏报(该抓没抓): {misses}"
    print("\n[1] 无漏报(>500 与低余额保护都抓住了)OK")

    # [2] 误报:日常消费不该被拦。阈值 500 后,220/320/450 都该放行
    #     (原默认 200 时它们全是误报 → 用户会被烦到关掉守门人)
    assert fp_rate <= 0.15, f"误报率过高({fp_rate:.0%}),用户会关掉守门人: {fps}"
    print(f"[2] 误报率 {fp_rate:.0%} ≤ 15%(沿用 evals_smoke 同款红线)OK")

    # [3] 误报**可归因**:这是"确定误报原因加以修改"的前提 ——
    #     若无法定位是哪条规则拦的,就无从改起。这里验证归因机制本身有效:
    #     故意把阈值压到 100 制造误报,必须能指出是 large_purchase_check 干的。
    pol_bad = _policy()
    for r in pol_bad.rules:
        if r.id == "large_purchase_check":
            r.threshold = 100.0
    _m2, fps2 = evaluate(CASES, pol_bad)
    assert fps2, "阈值压到 100 应制造出误报(用于验证归因)"
    assert all("large_purchase_check" in rules for _, _, _, rules in fps2), \
        f"误报必须能归因到具体规则,否则无从改起: {fps2}"
    print(f"[3] 误报可归因(阈值压到100 → {len(fps2)} 条误报,全部指向 large_purchase_check)OK")

    # [4] 规则实例隔离(本框架第一次跑就抓到的真 bug):
    #     原 `self.rules = list(DEFAULT_RULES)` 只复制列表、不复制规则对象 → 所有 runtime
    #     共享同一批**可变**规则,[3] 里把 pol_bad 阈值压到 100 竟连带把 pol 和模块级
    #     DEFAULT_RULES 一起改了(要重启才恢复)。现已 deepcopy。
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

    # [5] 阈值是可调项(改 yaml 即可,不用动代码);默认 500
    assert rd.threshold == 500.0, f"默认单笔阈值应为 500,实际 {rd.threshold}"
    print("[5] 阈值为可调项(finance_policy.yaml 可改,默认 500)OK")

    # ══════════════════════════════════════════════════════════
    # 用户点名要的 4 条规则(2026-07-16)。前两条的检测早就实现,只是从不产生裁决。
    # ══════════════════════════════════════════════════════════
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

    # [6] 同一商家高频扣款 → 必须裁决(此前 detect_duplicate_charges 算了却不吭声)
    dups = FR.detect_duplicate_charges([
        _txn(-99, "SomeApp", days_ago=1), _txn(-99, "SomeApp", days_ago=1),
    ])
    assert dups, "重复扣款应被检测到"
    assert "duplicate_charge_alert" in _fire(duplicates=dups), "检测到却不裁决(老毛病)"
    assert "duplicate_charge_alert" not in _fire(duplicates=[]), "无重复扣款不该报警"
    print(f"[6] 同一商家高频扣款 → 触发裁决({dups[0]['reason']})OK")

    # [7] 跨月预算超支**趋势**:还没超、但按速度月底会超 → 提醒;已超/不会超 → 不重复报
    f_trend = {"category": "food", "spent_so_far": 800, "monthly_limit": 1000,
               "projected_month_end": 1600, "projected_overage": 600, "will_overspend": True}
    f_safe = {"category": "food", "spent_so_far": 200, "monthly_limit": 1000,
              "projected_month_end": 400, "projected_overage": 0, "will_overspend": False}
    assert "overspend_trend_alert" in _fire(forecasts=[f_trend]), "超支趋势应提醒"
    assert "overspend_trend_alert" not in _fire(forecasts=[f_safe]), "不会超不该提醒"
    print("[7] 跨月超支趋势(还没超但会超 → 提醒;不会超 → 不吵)OK")

    # [8] 年费自动续订**前**提醒:从历史间隔推断周期,只在临近时提醒
    # 上次扣款 362 天前、上上次 727 天前 → 间隔 365(年订)→ 下次在 3 天后
    annual = [_txn(-598, "某会员", days_ago=362 + 365, tags=["recurring"]),
              _txn(-598, "某会员", days_ago=362, tags=["recurring"])]
    ren = FR.detect_upcoming_renewals(annual, horizon_days=7)
    assert ren and ren[0]["cadence"] == "年订" and ren[0]["days_until"] <= 7, ren
    assert "renewal_reminder" in _fire(renewals=ren)
    # 间隔紊乱 → 不预测(宁可不提醒,也不为猜错的日期制造误报)
    messy = [_txn(-50, "乱扣", days_ago=200, tags=["recurring"]),
             _txn(-50, "乱扣", days_ago=3, tags=["recurring"])]
    assert FR.detect_upcoming_renewals(messy) == [], "间隔紊乱不该预测续订日"
    # 只扣过一次 → 无法判周期
    assert FR.detect_upcoming_renewals([_txn(-50, "新订阅", days_ago=1, tags=["recurring"])]) == []
    print(f"[8] 年费续订前提醒({ren[0]['entity']} {ren[0]['days_until']} 天后;紊乱/单次不预测)OK")

    # [9] 退款未到账:**需用户标记** refund_expected;已到账则不报
    overdue = FR.detect_pending_refunds([_txn(-300, "某电商", days_ago=20, tags=["refund_expected"])])
    assert overdue and overdue[0]["days_waiting"] >= 14, overdue
    assert "refund_overdue_alert" in _fire(pending_refunds=overdue)
    # 等额退款已入账 → 不报
    done = FR.detect_pending_refunds([_txn(-300, "某电商", days_ago=20, tags=["refund_expected"]),
                                      _txn(+300, "某电商", days_ago=2)])
    assert done == [], "退款已到账不该再报"
    # 【关键】未标记的普通消费**绝不**报警 —— 否则每笔消费都报,是灾难性误报
    assert FR.detect_pending_refunds([_txn(-300, "某电商", days_ago=20)]) == [], \
        "未标记的普通消费不得触发退款报警(灾难性误报)"
    print("[9] 退款未到账(需标记;已到账不报;未标记的普通消费不误报)OK")

    # [10] 新规则不得把日常消费变成噪音 —— 无信号时一条都不该响
    assert _fire() == set(), f"无信号时不该有任何裁决: {_fire()}"
    print("[10] 无信号 → 零裁决(新规则不制造背景噪音)OK")

    # ══════════════════════════════════════════════════════════
    # 阈值自校准:阈值不再是我拍的,而是从用户自己的消费分布长出来的。
    # 这一段验的是"没有真实数据时也不会瞎猜、有数据时自动贴合、且永不踩人的决定"。
    # ══════════════════════════════════════════════════════════
    def _stream(each, n=60, entity="日常"):
        """造 n 笔支出,跨**正好** n-1 天(满足校准的样本门槛)。

        基准时刻只取一次:若每笔各调 datetime.now(),60 次调用间的微秒漂移会把跨度
        啃掉不到一天,而 .days 向下取整 → span 变成 58,校准值随机器快慢漂移。
        """
        base = datetime.now()
        return [Transaction(amount=-each, category="misc", entity=entity, account="default",
                            timestamp=(base - timedelta(days=i)).isoformat())
                for i in range(n)]

    # [11] 样本不足 → 不校准(继续用出厂默认)
    assert FR.calibrate_thresholds([]) == {}, "空数据不该校准"
    assert FR.calibrate_thresholds(_stream(300, n=5)) == {}, "5 笔就校准 = 用垃圾数据冒充个性化"
    assert FR.calibrate_thresholds(_stream(300, n=30)) == {}, \
        "笔数够但跨度只有 29 天(差一天到门槛)→ 日均不可信,不该校准"
    print("[11] 样本不足(空/笔数少/跨度短)→ 不校准,沿用出厂默认 OK")

    # 有方差的真实感消费流:大量小额 + 可选一笔大额尖峰。均匀数据测不出"抗离群"。
    _PAT = [30, 45, 60, 80, 120, 55, 90, 40, 150, 70]   # median≈65 的日常波动
    def _real(scale=1.0, n=80, spike=None):
        base = datetime.now()
        txns = [Transaction(amount=-_PAT[i % len(_PAT)] * scale, category="misc",
                            entity="日常", account="default",
                            timestamp=(base - timedelta(days=i)).isoformat())
                for i in range(n)]
        if spike:      # 一笔大额(买电脑/交房租),落在窗口内,不改变跨度
            txns.append(Transaction(amount=-spike, category="misc", entity="大额",
                        account="default", timestamp=(base - timedelta(days=1)).isoformat()))
        return txns

    # [12] 校准随消费水平伸缩 —— 这正是固定 500 做不到的事
    rich = FR.calibrate_thresholds(_real(scale=2.0))     # 消费翻倍的人
    poor = FR.calibrate_thresholds(_real(scale=0.5))     # 消费减半的人
    lr, lp = rich["large_purchase_check"][0], poor["large_purchase_check"][0]
    assert lr > lp, f"高消费者的大额门槛应更高: 富{lr} vs 穷{lp}"
    assert rich["low_balance_protection"][0] > poor["low_balance_protection"][0]
    print(f"[12] 阈值随消费水平伸缩(富→大额{lr:.0f} / 穷→大额{lp:.0f};固定 500 对两者都错)OK")

    # [12b]【B 修复的直接证据】大额离群**不**抬高门槛 ——
    #      这是把均值锚换成 median+MAD 的全部理由:一笔 8000 的电脑不该让此后的
    #      大额检测变迟钝。均值锚在这里会让门槛暴涨(月均×10% 从 ~200 跳到 ~500)。
    base_large = FR.calibrate_thresholds(_real())["large_purchase_check"][0]
    spiked_large = FR.calibrate_thresholds(_real(spike=8000))["large_purchase_check"][0]
    assert abs(spiked_large - base_large) <= 100, \
        f"一笔大额离群把门槛从 {base_large} 顶到 {spiked_large}(均值锚的老毛病,MAD 应免疫)"
    print(f"[12b] 抗离群:加一笔 8000 大额,门槛 {base_large:.0f}→{spiked_large:.0f} 几乎不动 OK")

    # [13] 时间类阈值**不**校准(它们是语义常数,不是从消费分布学得出来的)
    assert set(rich) == {"large_purchase_check", "low_balance_protection"}, \
        f"只有金额类阈值有分布依据;时间类不得假装校准: {set(rich)}"
    print("[13] 时间类阈值(48h/7d/14d)不参与校准(无分布依据,不给猜测套数学外衣)OK")

    # [14] 落盘:builtin 且停在出厂值 → 校准生效,且留下可解释的依据
    cal = FR.calibrate_thresholds(_real())
    cal_large = cal["large_purchase_check"][0]
    p14 = _policy()
    ch = p14.apply_calibration(cal)
    r14 = [r for r in p14.rules if r.id == "large_purchase_check"][0]
    assert r14.threshold == cal_large and r14.source == "learned" and r14.calibrated_value == cal_large
    assert "median" in (r14.calibrated_from or ""), "阈值必须可解释,用户才敢信"
    # large 一定变了;low_balance 恰好也算出 500(=出厂)时不进 ch —— 那正是"值没变不写盘"
    assert "large_purchase_check" in {c["rule_id"] for c in ch}, ch
    # 幂等:同样的建议再来一次 → 无改动、不重复写盘(analyze 每次都会调到)
    assert p14.apply_calibration(cal) == [], "值没变还写盘 = 每次 analyze 都刷一遍 yaml"
    print(f"[14] 校准落盘 + 可解释({r14.calibrated_from})+ 幂等 OK")

    # [15]【最重要】校准**绝不踩人的决定** —— 否则用户会发现自己改的设置莫名变回去,
    #     从此不信任整个系统。三种"人碰过"的形态都必须让校准收手:
    p_user = _policy()                       # (a) source=user 显式锁定
    for r in p_user.rules:
        if r.id == "large_purchase_check": r.source = "user"
    p_user.apply_calibration(rich)
    assert [r for r in p_user.rules if r.id == "large_purchase_check"][0].threshold == 500.0, \
        "source=user 是显式锁定,校准不得覆盖"

    p_edit = _policy()                       # (b) builtin 但用户改过 yaml 阈值
    for r in p_edit.rules:
        if r.id == "large_purchase_check": r.threshold = 1234.0
    p_edit.apply_calibration(rich)
    assert [r for r in p_edit.rules if r.id == "large_purchase_check"][0].threshold == 1234.0, \
        "阈值偏离出厂值 = 有人有意改过,校准必须收手"

    p_re = _policy()                         # (c) 我校准过,之后用户又手改
    p_re.apply_calibration(rich)
    for r in p_re.rules:
        if r.id == "large_purchase_check": r.threshold = 777.0
    p_re.apply_calibration(poor)
    assert [r for r in p_re.rules if r.id == "large_purchase_check"][0].threshold == 777.0, \
        "校准值被人改过 = 人的决定,不得再次覆盖"
    print("[15] 校准不踩人的决定(user 锁定 / 手改出厂值 / 手改校准值 → 三种都收手)OK")

    # [16] 校准不污染模块级出厂默认(自校准直接写 rule.threshold,最容易复发 [4] 那个 bug)
    assert rd.threshold == 500.0, f"校准污染了模块级 DEFAULT_RULES(阈值变成 {rd.threshold})"
    from foxclaw.finance.policy import FACTORY_THRESHOLDS
    assert FACTORY_THRESHOLDS["large_purchase_check"] == 500.0, "出厂快照被污染 → 判定基准失效"
    print("[16] 校准不污染模块级出厂默认/出厂快照 OK")

    # [17] 校准必须**对用户可见**(阈值自己变了还不吭声 = 偷偷改用户的设置)
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

    # ══════════════════════════════════════════════════════════
    # 深审补丁(2026-07-16):A 月初误报闸 / C 续订阈值真正生效。
    # 这两条是"审核后面做的"翻出来的真问题——不是新功能,是把已埋的雷拆掉。
    # ══════════════════════════════════════════════════════════

    # [18]【A】月初超支外推不可信 → 不报。overspend_forecast 用 spent/day,day=1 时
    #     一笔正常消费被 ×30 放大成"月底将超支"。前 7 天标 reliable=False,裁决层闭嘴。
    from foxclaw.finance.models import Transaction as _T
    def _jul(day, amt):    # 落在 2026-07 的一笔支出
        return _T(amount=-amt, category="food", entity="午饭", account="default",
                  timestamp=f"2026-07-{day:02d}T12:00:00")
    food_budget = Budget(category="food", monthly_limit=1000)
    # 月初第 2 天,只花了 100:daily_rate=50 → 外推 1500 > 1000,老逻辑会喊"将超支"
    early = FR.overspend_forecast([_jul(1, 100)], food_budget,
                                  now=datetime(2026, 7, 2))
    assert early["will_overspend"] and not early["reliable"], early
    assert "overspend_trend_alert" not in _fire(forecasts=[early]), "月初噪音不得报警(A 修复)"
    # 到第 20 天,同样趋势才可信
    late = FR.overspend_forecast([_jul(d, 100) for d in range(1, 16)], food_budget,
                                 now=datetime(2026, 7, 20))
    assert late["reliable"], late
    print("[18]【A】月初(day<7)超支外推 reliable=False → 不误报;7 天后才裁决 OK")

    # [19]【C】续订提醒阈值真正生效。检测层窗口放宽到 35 天,"提前几天提醒"由规则
    #     threshold 精确控制。旧代码里 detect 硬封 7 天 → 用户改阈值到 25 也没反应。
    monthly_sub = [_txn(-30, "月刊会员", days_ago=10 + 30, tags=["recurring"]),
                   _txn(-30, "月刊会员", days_ago=10, tags=["recurring"])]
    ren = FR.detect_upcoming_renewals(monthly_sub)        # 下次在约 20 天后
    assert ren and ren[0]["cadence"] == "月订" and 18 <= ren[0]["days_until"] <= 22, ren
    assert "renewal_reminder" not in _fire(renewals=ren), "默认提前 7 天 → 20 天后的续订还太远,不报"
    p19 = _policy()                                        # 用户想提前 25 天提醒
    for r in p19.rules:
        if r.id == "renewal_reminder": r.threshold = 25.0
    v19 = {x.rule_id for x in p19.evaluate_state(renewals=ren)}
    assert "renewal_reminder" in v19, "阈值改到 25 应生效(旧代码被检测层硬封 7 天 → 静默失效)"
    print("[19]【C】续订阈值真正生效(默认7天不报20天后的;改25天则报)OK")

    # [20]【D】退款消耗式匹配:两笔同额 refund_expected、只收到一笔退款 → 第二笔仍报。
    #     旧的 any() 匹配让两笔支出都认领同一笔入账 → 第二笔漏报(以为退了,其实没到)。
    two_expected = [
        _txn(-300, "某电商", days_ago=40, tags=["refund_expected"]),
        _txn(-300, "某电商", days_ago=30, tags=["refund_expected"]),
        _txn(+300, "某电商", days_ago=5),          # 只到账一笔退款
    ]
    pend = FR.detect_pending_refunds(two_expected)
    assert len(pend) == 1, f"两笔标记只退一笔 → 应恰好报 1 笔未到账,实得 {len(pend)}: {pend}"
    # 两笔都到账 → 零报警(两笔入账各抵一笔)
    both_back = two_expected[:2] + [_txn(+300, "某电商", days_ago=5),
                                    _txn(+300, "某电商", days_ago=4)]
    assert FR.detect_pending_refunds(both_back) == [], "两笔退款都到账不该报"
    print("[20]【D】退款消耗式匹配(两笔同额退一笔→报第二笔;都退→不报)OK")

    # [21]【E】并发校准无竞态、yaml 不写坏。finance_runtime 是单例,analyze() 会被请求
    #     线程并发调 → apply_calibration 并发改规则+写盘。写锁 + 原子 rename 保证安全。
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
    # 恰好一个线程做了首次校准(拿到非空 changed),其余读到已校准状态(空)
    assert sum(1 for s in seen_to if s) == 1, \
        f"首次校准应只发生一次(其余幂等空),实得 {sum(1 for s in seen_to if s)} 次非空"
    # yaml 落盘完好可重载,且阈值 = 校准值(没被并发写坏)
    reload21 = FinancePolicyEngine(str(rt21.policy.policy_path))
    r21 = [r for r in reload21.rules if r.id == "large_purchase_check"][0]
    assert r21.threshold == cal_large and r21.source == "learned", \
        f"并发后 yaml 落盘损坏或值不对: threshold={r21.threshold}"
    print(f"[21]【E】16 线程并发校准:无异常 · 首次只一次 · yaml 完好(large={r21.threshold:.0f})OK")

    print("\n领域推理评测通过")

if __name__ == "__main__":
    test_domain_evals()
