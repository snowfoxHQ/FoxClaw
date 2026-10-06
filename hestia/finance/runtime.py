"""
Hestia 财务子系统 — Finance Runtime
把 记忆 + 推理 + Policy 串成一个可自治的财务内核。
执行层只做"建议 + 审批流",真正动钱的操作留接口、不自动执行。
"""

import csv
from datetime import datetime
from pathlib import Path

from .models import Transaction, Budget, FinancialEntity
from .reasoning import FinanceReasoning
from .policy import FinancePolicyEngine


class FinanceRuntime:
    """财务 Agent 的资金行为操作系统内核。"""

    def __init__(self, policy_path: str = "./finance_policy.yaml", hmr=None):
        self.transactions: list = []
        self.budgets: list = []
        self.entities: dict = {}            # name -> FinancialEntity
        self.account_balances: dict = {}    # account -> balance
        self.policy = FinancePolicyEngine(policy_path)
        self.reasoning = FinanceReasoning()
        self.hmr = hmr                      # 可选:把交易写入 HMR 共享认知

    # ── 数据录入 ──────────────────────────────────────────
    def add_transaction(self, txn: Transaction,
                        run_gatekeeper: bool = True) -> dict:
        """录入一笔交易,经过守门人裁决。"""
        self.transactions.append(txn)
        # 更新实体统计
        ent = self.entities.get(txn.entity)
        if not ent:
            etype = "subscription" if txn.is_recurring else "merchant"
            ent = FinancialEntity(name=txn.entity, entity_type=etype)
            self.entities[txn.entity] = ent
        ent.txn_count += 1
        ent.total_amount += txn.amount
        ent.last_seen = txn.timestamp
        if txn.is_recurring:
            ent.recurring_amount = abs(txn.amount)

        # 写入 HMR(可选)
        if self.hmr:
            try:
                self.hmr.ingest(
                    content=f"交易:{txn.entity} {txn.amount} ({txn.category})",
                    type="execution", agent_id="finance",
                    title=f"txn_{txn.entity}", tags=["transaction"] + txn.tags,
                )
            except Exception:
                pass

        # 守门人裁决
        verdicts = []
        if run_gatekeeper:
            bal = self.account_balances.get(txn.account)
            verdicts = self.policy.evaluate_transaction(txn, bal)
        return {
            "transaction": txn.to_dict(),
            "verdicts": [v.to_dict() for v in verdicts],
            "gated": any(v.action in ("veto", "freeze", "require_confirmation")
                         for v in verdicts),
        }

    def set_budget(self, category: str, monthly_limit: float):
        self.budgets = [b for b in self.budgets if b.category != category]
        self.budgets.append(Budget(category=category, monthly_limit=monthly_limit))

    def set_balance(self, account: str, balance: float):
        self.account_balances[account] = balance

    def import_csv(self, path: str) -> int:
        """从 CSV 导入交易。列:amount,category,entity,account,timestamp,tags
        真实银行 API 接口可在此处替换/扩展。"""
        path = Path(path)
        count = 0
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                tags = [t.strip() for t in (row.get("tags") or "").split("|") if t.strip()]
                txn = Transaction(
                    amount=float(row["amount"]),
                    category=row.get("category", "uncategorized"),
                    entity=row.get("entity", "unknown"),
                    account=row.get("account", "default"),
                    timestamp=row.get("timestamp") or datetime.now().isoformat(),
                    tags=tags,
                )
                self.add_transaction(txn, run_gatekeeper=False)
                count += 1
        return count

    # ── 综合分析(财务 Agent 的核心输出)──────────────────
    def analyze(self, period: str = None, current_balance: float = None,
                calibrate: bool = True) -> dict:
        """一次性产出完整财务洞察:预算/风险/预测/守门人裁决。

        calibrate=True 时先按用户自己的消费分布校准金额类阈值(样本不足则原样不动),
        再裁决 —— 让阈值随消费水平自己长,而不是永远停在出厂拍的经验值上。
        """
        period = period or datetime.now().strftime("%Y-%m")
        bal = current_balance
        if bal is None and self.account_balances:
            bal = sum(self.account_balances.values())

        # 阈值自校准:必须在裁决**之前**,否则这次分析用的还是上一轮的旧阈值
        calibration = (self.policy.apply_calibration(
            self.reasoning.calibrate_thresholds(self.transactions)) if calibrate else [])

        budget_status = self.reasoning.budget_status(
            self.transactions, self.budgets, period)
        anomalies = self.reasoning.detect_anomalies(self.transactions)
        duplicates = self.reasoning.detect_duplicate_charges(self.transactions)
        sub_creep = self.reasoning.detect_subscription_creep(self.transactions)
        burn = self.reasoning.burn_rate(self.transactions)
        cashflow = (self.reasoning.cashflow_projection(self.transactions, bal)
                    if bal is not None else None)

        # 超支预测(对每个有预算的分类)—— 提到裁决之前算,好一并交给守门人
        forecasts = [self.reasoning.overspend_forecast(self.transactions, b, period)
                     for b in self.budgets]
        renewals = self.reasoning.detect_upcoming_renewals(self.transactions)
        pending_refunds = self.reasoning.detect_pending_refunds(self.transactions)

        # 守门人对整体状态裁决
        # duplicates/forecasts 此前已被算出来、却只进报告不进裁决(检测到了却不吭声);
        # renewals/pending_refunds 为新增信号。现在一并交给守门人。
        state_verdicts = self.policy.evaluate_state(
            subscription_total=sub_creep["monthly_total"], anomalies=anomalies,
            duplicates=duplicates, forecasts=forecasts,
            renewals=renewals, pending_refunds=pending_refunds)

        # 结构化证据链(可解释决策,可沉淀到 ThoughtChain)
        from .evidence import FinanceExplainer
        evidence_chains = [
            FinanceExplainer.explain_budget_risk(budget_status).to_dict(),
            FinanceExplainer.explain_anomalies(anomalies).to_dict(),
        ]

        return {
            "period": period,
            "transaction_count": len(self.transactions),
            "budget_status": budget_status,
            "overspend_forecast": forecasts,
            "anomalies": anomalies,
            "duplicate_charges": duplicates,
            "subscription_creep": sub_creep,
            "burn_rate": burn,
            "cashflow_projection": cashflow,
            "gatekeeper_verdicts": [v.to_dict() for v in state_verdicts],
            "threshold_calibration": calibration,   # 阈值自己变了要让用户看见,不能偷偷改
            "evidence_chains": evidence_chains,
        }

    # ── 守门人审批流(不自动动钱)─────────────────────────
    def request_action(self, action_type: str, detail: dict) -> dict:
        """请求一个资金操作(如取消订阅、转账)。
        不自动执行——返回审批请求,标记需用户确认 / 需接真实支付 API。"""
        return {
            "action": action_type,
            "detail": detail,
            "status": "PENDING_USER_APPROVAL",
            "note": "Hestia 不自动执行资金操作。请用户确认,或接入真实支付/银行 API 后执行。",
        }

    def status(self) -> dict:
        return {
            "transactions": len(self.transactions),
            "budgets": len(self.budgets),
            "entities": len(self.entities),
            "accounts": self.account_balances,
            "policy": self.policy.summary(),
        }

    def graph(self) -> "FinancialDataGraph":
        """构建财务数据图(交易/分类/商户/账户/预算关联)。"""
        from .graph import FinancialDataGraph
        g = FinancialDataGraph()
        g.build(self.transactions, self.budgets)
        return g

    def trace_category(self, category: str) -> dict:
        """关系查询:追溯某分类的消费成因(谁造成的、花在谁身上)。"""
        return self.graph().trace_overspend(category)

    def reconcile(self, external_records: list, tolerance_days: int = 3) -> dict:
        """对账:把系统交易与外部流水(银行/支付宝账单)交叉核对。

        external_records: [ExternalRecord] 或 [dict(amount,entity,date,account,ref)]。
        返回:匹配/差异分类 + 审计日志。
        """
        from .reconcile import Reconciler, ExternalRecord
        # 容忍 dict 输入,自动转 ExternalRecord
        ext = []
        for r in external_records:
            if isinstance(r, ExternalRecord):
                ext.append(r)
            elif isinstance(r, dict):
                ext.append(ExternalRecord(
                    amount=r.get("amount", 0), entity=r.get("entity", ""),
                    date=r.get("date", ""), account=r.get("account", ""),
                    ref=r.get("ref", "")))
        rec = Reconciler(tolerance_days=tolerance_days)
        return rec.reconcile(self.transactions, ext).to_dict()
