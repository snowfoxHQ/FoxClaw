"""
Foxclaw 财务子系统 — Finance Runtime
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
from .fx import FxTable
from .nlparse import CATEGORY_LABELS, parse as parse_expense_text


def _period_of(txn, default: str = "") -> str:
    """交易的账期(YYYY-MM)。时间戳坏掉时返回 default,不让一条脏数据炸掉整个汇总。"""
    try:
        return txn.timestamp[:7]
    except Exception:
        return default


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
                 data_path: str | None = None):
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
        # 多币种:账本按本位币(CNY)算账,汇率表与账本同目录
        self.fx = FxTable(str(self._data_path.parent / "finance_fx.json"))
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

    # ── 自然语言记账(第一线功能)────────────────────────────
    def record_text(self, text: str, account: str = "default",
                    now: datetime | None = None, dry_run: bool = False) -> dict:
        """「今天买了一件衣服花了 105 块」→ 落一笔账,返回可读回执。

        识别(金额/币种/时间/分类)全部走 `nlparse` 的确定性规则,LLM 不参与算钱。
        未来时间(明天/下个月)记成 planned,进账本但不进"本月已花"。
        """
        parsed = parse_expense_text(text, now=now)
        if not parsed:
            return {"recorded": [], "skipped": [{"text": (text or "").strip(),
                                                 "reason": "没识别出金额"}],
                    "dry_run": dry_run, "ack": "这句话里没听出金额,没有记账。"}
        recorded = []
        for p in parsed:
            sign = -1 if p.direction == "expense" else 1
            base, rate = self.fx.to_base(p.amount, p.currency)
            tags = []
            if p.planned:
                tags.append("planned")
            if p.direction == "income":
                tags.append("income")
            txn = Transaction(
                amount=round(sign * base, 2),
                category=p.category,
                entity=p.entity or CATEGORY_LABELS.get(p.category, "未注明"),
                account=account,
                timestamp=p.when.isoformat(timespec="seconds"),
                tags=tags,
                note=p.text,
                currency=p.currency,
                original_amount=round(sign * p.amount, 2),
                fx_rate=rate,
                description=p.description,
                status="planned" if p.planned else "settled",
                source="text",
            )
            item = {**txn.to_dict(), "confidence": p.confidence,
                    "warnings": list(p.warnings)}
            if not self.fx.known(p.currency):
                item["warnings"].append(
                    f"未配置 {p.currency} 汇率,暂按 1:1 记入,可用 /api/finance/fx 设置")
            if dry_run:
                item["verdicts"] = []
            else:
                res = self.add_transaction(txn, run_gatekeeper=True, save=False)
                item["verdicts"] = res["verdicts"]
            recorded.append(item)
        if recorded and not dry_run:
            self.save()
        result = {"recorded": recorded, "skipped": [], "dry_run": dry_run}
        result["ack"] = self.render_ack(result)
        return result

    def render_ack(self, rec: dict) -> str:
        """把记账结果拼成人话。确定性文本 —— 记账回执不该等 LLM,也不该被它改写。"""
        items = rec.get("recorded") or []
        if not items:
            return "这句话里没听出金额,没有记账。"
        head = "预览(未落账)" if rec.get("dry_run") else "已记账"
        lines = [f"{head} {len(items)} 笔:"]
        for it in items:
            cur = it.get("currency") or "CNY"
            orig = it.get("amount_original") or 0.0
            amt = it.get("amount") or 0.0
            label = CATEGORY_LABELS.get(it.get("category"), it.get("category"))
            flag = "(计划)" if it.get("status") == "planned" else ""
            money = (f"{orig:+.2f} {cur}(≈¥{amt:+.2f})" if cur != "CNY"
                     else f"{amt:+.2f} 元")
            day = (it.get("timestamp") or "")[:10]
            lines.append(f"· {day} {it.get('description') or it.get('entity') or ''}"
                         f" {money} · {label}{flag}")

        # 汇总期跟着这笔账走:记的是上月 5 号的账,就别报"本月已花"
        period = (items[0].get("timestamp") or "")[:7] or None
        led = self.ledger(period)
        head = "本月" if period == datetime.now().strftime("%Y-%m") else f"{period} "
        lines.append(f"{head}已花 ¥{led['expense']:.2f},共 {led['count']} 笔,"
                     f"净额 ¥{led['net']:+.2f}")
        if led["planned"]["count"]:
            lines.append(f"另有 {led['planned']['count']} 笔计划支出共 "
                         f"¥{led['planned']['expense']:.2f},发生时才算进已花")
        gates = [v for it in items for v in (it.get("verdicts") or [])
                 if v.get("action") in ("veto", "freeze", "require_confirmation")]
        for v in gates:
            lines.append(f"守门人:{v.get('action')} — "
                         f"{v.get('message') or v.get('reason') or ''}")
        warns = list(dict.fromkeys(w for it in items for w in (it.get("warnings") or [])))
        if warns:
            lines.append("提示:" + "；".join(warns))
        return "\n".join(lines)

    def ledger(self, period: str | None = None) -> dict:
        """记账本:按月汇总(已发生 vs 计划、按天、按分类、按币种)。"""
        period = period or datetime.now().strftime("%Y-%m")
        with self._lock:
            txns = list(self.transactions)
        settled = [t for t in txns
                   if not getattr(t, "is_planned", False) and _period_of(t) == period]
        planned = sorted((t for t in txns if getattr(t, "is_planned", False)),
                         key=lambda t: t.timestamp)

        expense = round(sum(t.amount for t in settled if t.amount < 0), 2)
        income = round(sum(t.amount for t in settled if t.amount > 0), 2)

        by_day, by_cat, by_cur = {}, {}, {}
        for t in settled:
            day = (t.timestamp or "")[:10]
            d = by_day.setdefault(day, {"date": day, "expense": 0.0, "income": 0.0,
                                        "count": 0, "items": []})
            d["count"] += 1
            if t.amount < 0:
                d["expense"] = round(d["expense"] + t.amount, 2)
            else:
                d["income"] = round(d["income"] + t.amount, 2)
            if len(d["items"]) < 6:
                d["items"].append(t.description or t.entity)

            if t.amount < 0:
                c = by_cat.setdefault(t.category, {"category": t.category,
                                                   "label": CATEGORY_LABELS.get(t.category, t.category),
                                                   "total": 0.0, "count": 0})
                c["total"] = round(c["total"] + t.amount, 2)
                c["count"] += 1

            cur = getattr(t, "currency", "CNY") or "CNY"
            k = by_cur.setdefault(cur, {"currency": cur, "original_total": 0.0,
                                        "base_total": 0.0, "count": 0})
            k["original_total"] = round(k["original_total"] + t.amount_original, 2)
            k["base_total"] = round(k["base_total"] + t.amount, 2)
            k["count"] += 1

        cat_total = abs(sum(c["total"] for c in by_cat.values())) or 1.0
        categories = sorted(by_cat.values(), key=lambda c: c["total"])
        for c in categories:
            c["share"] = round(abs(c["total"]) / cat_total, 3)
            c["total"] = round(abs(c["total"]), 2)

        def _entry(t):
            return {"id": t.id, "date": (t.timestamp or "")[:10],
                    "description": t.description or t.entity, "entity": t.entity,
                    "category": t.category,
                    "category_label": CATEGORY_LABELS.get(t.category, t.category),
                    "amount": t.amount, "currency": getattr(t, "currency", "CNY"),
                    "amount_original": t.amount_original,
                    "status": getattr(t, "status", "settled"),
                    "note": t.note}

        return {
            "period": period,
            "base_currency": "CNY",
            "count": len(settled),
            "expense": round(abs(expense), 2),
            "income": round(income, 2),
            "net": round(income + expense, 2),
            "by_day": sorted(by_day.values(), key=lambda d: d["date"], reverse=True),
            "by_category": sorted(categories, key=lambda c: -c["total"]),
            "by_currency": sorted(by_cur.values(), key=lambda c: -abs(c["base_total"])),
            "planned": {
                "count": len(planned),
                "expense": round(abs(sum(t.amount for t in planned if t.amount < 0)), 2),
                "items": [_entry(t) for t in planned],
            },
            "entries": [_entry(t) for t in sorted(settled, key=lambda t: t.timestamp,
                                                  reverse=True)][:300],
        }

    def import_csv(self, path: str) -> int:
        """从 CSV 导入交易。列:amount,category,entity,account,timestamp,tags
        真实银行 API 接口可在此处替换/扩展。"""
        path = Path(path)
        count = 0
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                tags = [t.strip() for t in (row.get("tags") or "").split("|") if t.strip()]
                # 可选的 currency 列:外币行按当天记账汇率折成本位币再入账,
                # 否则「100 美元」会和「100 元」一起被求和。
                cur = (row.get("currency") or row.get("币种") or "CNY").strip().upper()
                raw = float(row["amount"])
                base, rate = self.fx.to_base(abs(raw), cur)
                txn = Transaction(
                    amount=round(-base if raw < 0 else base, 2),
                    category=row.get("category", "uncategorized"),
                    entity=row.get("entity", "unknown"),
                    account=row.get("account", "default"),
                    timestamp=row.get("timestamp") or datetime.now().isoformat(),
                    tags=tags,
                    currency=cur, original_amount=raw, fx_rate=rate,
                    description=row.get("description", ""),
                    source="csv",
                )
                self.add_transaction(txn, run_gatekeeper=False, save=False)
                count += 1
        if count:
            self.save()      # 批量导入只落盘一次,不按每笔交易各做一次磁盘 IO
        return count

    # ── 综合分析(财务 Agent 的核心输出)──────────────────
    def analyze(self, period: str | None = None, current_balance: float | None = None,
                calibrate: bool = True) -> dict:
        """一次性产出完整财务洞察:预算/风险/预测/守门人裁决。

        calibrate=True 时先按用户自己的消费分布校准金额类阈值(样本不足则原样不动),
        再裁决 —— 让阈值随消费水平自己长,而不是永远停在出厂拍的经验值上。
        """
        period = period or datetime.now().strftime("%Y-%m")
        # 快照:分析期间可能有并发录入(持久化后 import 端点就是可并发的写方),
        # 直接遍历 self.transactions 会撞上 "list changed size during iteration"。
        # 计划支出(明天/下个月)必须排除:还没发生的钱不能算进"本月已花",
        # 否则「明天交房租 3000」会立刻把这个月的预算打爆。
        txns = [t for t in self.transactions if not getattr(t, "is_planned", False)]
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
            "note": "Foxclaw 不自动执行资金操作。请用户确认,或接入真实支付/银行 API 后执行。",
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
