"""
Snowfoxclaw 财务子系统 — Finance Runtime
把 记忆 + 推理 + Policy 串成一个可自治的财务内核。
执行层只做"建议 + 审批流",真正动钱的操作留接口、不自动执行。
"""

import csv
import json
import threading
from dataclasses import fields
from datetime import datetime
from pathlib import Path

from .models import Transaction, Budget, FinancialEntity
from .reasoning import FinanceReasoning
from .policy import FinancePolicyEngine
from .graph import FinancialDataGraph


def _coerce(cls, d):
    """按 dataclass 字段过滤后构造;缺必填字段/类型不符 → None。

    只取认识的字段,是为了让**旧版本写下的文件**照样读得进来(多出的派生字段被忽略),
    而不是一升级就整份数据作废。
    """
    if not isinstance(d, dict):
        return None
    names = {f.name for f in fields(cls)}
    try:
        return cls(**{k: v for k, v in d.items() if k in names})
    except (TypeError, ValueError):
        return None


class FinanceRuntime:
    """财务 Agent 的资金行为操作系统内核。"""

    def __init__(self, policy_path: str = "./finance_policy.yaml", hmr=None,
                 data_path: str = None):
        self.transactions: list = []
        self.budgets: list = []
        self.entities: dict = {}            # name -> FinancialEntity
        self.account_balances: dict = {}    # account -> balance
        self.policy = FinancePolicyEngine(policy_path)
        self.reasoning = FinanceReasoning()
        self.hmr = hmr                      # 可选:把交易写入 HMR 共享认知
        # 交易/预算/余额/实体的落盘点:默认与 policy 同目录(调用方传的就是 data_dir)。
        # 此前这些只在内存,进程一重启就清零 → 财务分析永远是"0 笔交易"。
        self._data_path = Path(data_path) if data_path else (
            Path(policy_path).resolve().parent / "finance_data.json")
        # 写侧串行:runtime 是单例,请求线程会并发 add/import,analyze 还会触发阈值自校准写盘。
        self._lock = threading.RLock()
        self.load_error: str = ""
        self.save_error: str = ""
        self.load()

    # ── 持久化(原子写 tmp→replace;损坏文件只降级、不阻断启动)──────
    def load(self) -> int:
        """从落盘点恢复交易/预算/余额/实体,返回恢复的交易条数。

        容错原则:文件缺失/损坏/单条记录字段不合法一律跳过 —— 财务数据的问题
        不该把整个内核(进而是 Agent 启动)拖下水,大不了退回"0 笔交易"并留下 load_error。
        """
        self.transactions, self.budgets = [], []
        self.entities, self.account_balances = {}, {}
        self.load_error = ""
        p = self._data_path
        if not p.exists():
            return 0
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            self.load_error = f"{type(e).__name__}: {e}"
            return 0
        if not isinstance(data, dict):
            self.load_error = "顶层不是 JSON 对象"
            return 0

        self.transactions = [t for t in (_coerce(Transaction, d)
                                        for d in (data.get("transactions") or [])) if t]
        self.budgets = [b for b in (_coerce(Budget, d)
                                    for d in (data.get("budgets") or [])) if b]
        ents = data.get("entities") or {}
        if isinstance(ents, dict):
            for name, d in ents.items():
                ent = _coerce(FinancialEntity, d)
                if ent is not None:
                    self.entities[name] = ent
        bals = data.get("account_balances") or {}
        if isinstance(bals, dict):
            for acct, bal in bals.items():
                try:
                    self.account_balances[acct] = float(bal)
                except (TypeError, ValueError):
                    continue
        if not self.entities and self.transactions:
            self._rebuild_entities()        # 实体段缺失(旧格式/被裁)→ 按交易重算
        return len(self.transactions)

    def save(self) -> bool:
        """把当前状态原子写盘。失败只记 save_error、返回 False,绝不抛异常。"""
        with self._lock:
            data = {
                "version": 1,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "transactions": [t.to_dict() for t in self.transactions],
                "budgets": [b.to_dict() for b in self.budgets],
                "entities": {n: e.to_dict() for n, e in self.entities.items()},
                "account_balances": dict(self.account_balances),
            }
            try:
                self._data_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._data_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False),
                               encoding="utf-8")
                tmp.replace(self._data_path)
                self.save_error = ""
                # 文件已被本进程的完整快照覆盖 → 上一轮"读不进来"的原因作废(自愈)
                self.load_error = ""
                return True
            except Exception as e:
                # 落盘失败不能假装成功:留在内存里 + 暴露错误,让调用方/界面能看到
                self.save_error = f"{type(e).__name__}: {e}"
                return False

    def _rebuild_entities(self):
        """按已有交易重建实体统计(与 add_transaction 的累加规则一致)。"""
        for txn in self.transactions:
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

    # ── 数据录入 ──────────────────────────────────────────
    def add_transaction(self, txn: Transaction,
                        run_gatekeeper: bool = True,
                        save: bool = True) -> dict:
        """录入一笔交易,经过守门人裁决,并落盘(save=False 供批量导入只写一次)。"""
        with self._lock:
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
            result = {
                "transaction": txn.to_dict(),
                "verdicts": [v.to_dict() for v in verdicts],
                "gated": any(v.action in ("veto", "freeze", "require_confirmation")
                             for v in verdicts),
            }
        # 写盘放在锁外:避免一次磁盘 IO 把并发进来的读请求全堵住
        if save:
            self.save()
        return result

    def set_budget(self, category: str, monthly_limit: float):
        with self._lock:
            self.budgets = [b for b in self.budgets if b.category != category]
            self.budgets.append(Budget(category=category, monthly_limit=monthly_limit))
        self.save()

    def set_balance(self, account: str, balance: float):
        with self._lock:
            self.account_balances[account] = balance
        self.save()

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
                self.add_transaction(txn, run_gatekeeper=False, save=False)
                count += 1
        if count:
            self.save()      # 批量导入只落盘一次,不按每笔交易各做一次磁盘 IO
        return count

    # ── 综合分析(财务 Agent 的核心输出)──────────────────
    def analyze(self, period: str = None, current_balance: float = None,
                calibrate: bool = True) -> dict:
        """一次性产出完整财务洞察:预算/风险/预测/守门人裁决。

        calibrate=True 时先按用户自己的消费分布校准金额类阈值(样本不足则原样不动),
        再裁决 —— 让阈值随消费水平自己长,而不是永远停在出厂拍的经验值上。
        """
        period = period or datetime.now().strftime("%Y-%m")
        # 快照:分析期间可能有并发录入(持久化后 import 端点就是可并发的写方),
        # 直接遍历 self.transactions 会撞上 "list changed size during iteration"。
        txns = list(self.transactions)
        budgets = list(self.budgets)
        bal = current_balance
        if bal is None and self.account_balances:
            bal = sum(dict(self.account_balances).values())

        # 阈值自校准:必须在裁决**之前**,否则这次分析用的还是上一轮的旧阈值
        calibration = (self.policy.apply_calibration(
            self.reasoning.calibrate_thresholds(txns)) if calibrate else [])

        budget_status = self.reasoning.budget_status(txns, budgets, period)
        anomalies = self.reasoning.detect_anomalies(txns)
        duplicates = self.reasoning.detect_duplicate_charges(txns)
        sub_creep = self.reasoning.detect_subscription_creep(txns)
        burn = self.reasoning.burn_rate(txns)
        cashflow = (self.reasoning.cashflow_projection(txns, bal)
                    if bal is not None else None)

        # 超支预测(对每个有预算的分类)—— 提到裁决之前算,好一并交给守门人
        forecasts = [self.reasoning.overspend_forecast(txns, b, period)
                     for b in budgets]
        renewals = self.reasoning.detect_upcoming_renewals(txns)
        pending_refunds = self.reasoning.detect_pending_refunds(txns)

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
            "transaction_count": len(txns),
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
            "note": "Snowfoxclaw 不自动执行资金操作。请用户确认,或接入真实支付/银行 API 后执行。",
        }

    def status(self) -> dict:
        return {
            "transactions": len(self.transactions),
            "budgets": len(self.budgets),
            "entities": len(self.entities),
            "accounts": self.account_balances,
            "policy": self.policy.summary(),
            # 落盘状态一并回给界面:否则"数据到底存哪了/为什么重启就没了"只能靠猜
            "persist": {"path": str(self._data_path),
                        "exists": self._data_path.exists(),
                        "load_error": self.load_error,
                        "save_error": self.save_error},
        }

    def graph(self) -> "FinancialDataGraph":
        """构建财务数据图(交易/分类/商户/账户/预算关联)。"""
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
