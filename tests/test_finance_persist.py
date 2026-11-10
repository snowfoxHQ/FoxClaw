"""财务内核持久化 — 重启不丢账、坏文件不阻断启动、批量导入不逐笔写盘。

背景:交易/预算/余额原先只在内存,后端一重启就清零,财务分析永远是"0 笔交易"。
这里固定住三件事:
  1. 写进去的状态能被**新实例**读回来(真正的重启等价);
  2. 文件损坏 / 单条记录坏 / 落盘失败时,一律降级并把错误暴露出来,不抛、不阻断启动;
  3. 批量导入只做一次磁盘 IO。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.finance import FinanceRuntime, Transaction

CSV = str(Path(__file__).parent.parent / "examples" / "sample_transactions.csv")


def _rt(tmp_path, data_name="finance_data.json"):
    """临时目录里的 runtime:policy 与数据都写在 tmp_path,不污染工作区。"""
    return FinanceRuntime(policy_path=str(tmp_path / "finance_policy.yaml"),
                          data_path=str(tmp_path / data_name))


def test_落盘点默认与policy同目录(tmp_path):
    rt = FinanceRuntime(policy_path=str(tmp_path / "finance_policy.yaml"))
    assert rt._data_path == tmp_path / "finance_data.json"


def test_交易预算余额重启后可恢复(tmp_path):
    rt = _rt(tmp_path)
    rt.add_transaction(Transaction(amount=-88.5, category="food",
                                   entity="星巴克", account="debit",
                                   tags=["recurring"], note="早餐"))
    rt.add_transaction(Transaction(amount=12000, category="salary",
                                   entity="公司", account="debit"))
    rt.set_budget("food", 400)
    rt.set_balance("debit", 11800)

    assert rt.save() is True
    assert (tmp_path / "finance_data.json").exists()

    rt2 = _rt(tmp_path)
    assert len(rt2.transactions) == 2
    assert rt2.transactions[0].amount == -88.5
    assert rt2.transactions[0].entity == "星巴克"
    assert rt2.transactions[0].note == "早餐"
    assert rt2.transactions[0].tags == ["recurring"]
    assert rt2.transactions[1].amount == 12000
    assert len(rt2.budgets) == 1 and rt2.budgets[0].monthly_limit == 400
    assert rt2.account_balances == {"debit": 11800}
    assert rt2.entities["星巴克"].txn_count == 1
    assert rt2.entities["星巴克"].entity_type == "subscription"
    assert rt2.entities["公司"].total_amount == 12000


def test_写入即落盘下次实例自动加载(tmp_path):
    rt = _rt(tmp_path)
    rt.add_transaction(Transaction(amount=-20, category="transport",
                                   entity="地铁", account="debit"))
    rt.set_balance("debit", 100)

    loaded = _rt(tmp_path)
    assert loaded.load()
    assert len(loaded.transactions) == 1
    assert loaded.status()["persist"]["load_error"] == ""


def test_文件格式合法且带版本(tmp_path):
    rt = _rt(tmp_path)
    rt.add_transaction(Transaction(amount=-1, category="misc",
                                   entity="x", account="debit"))
    data = json.loads((tmp_path / "finance_data.json").read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["saved_at"]
    assert len(data["transactions"]) == 1


def test_损坏文件不阻断启动且可自愈(tmp_path):
    p = tmp_path / "finance_data.json"
    p.write_text("{ 这不是 json ", encoding="utf-8")

    rt = _rt(tmp_path)
    assert rt.transactions == [] and rt.budgets == []
    assert rt.load_error

    rt.add_transaction(Transaction(amount=-5, category="misc",
                                   entity="y", account="debit"))
    assert rt.save() is True
    assert rt.load_error == ""
    assert len(_rt(tmp_path).transactions) == 1


def test_顶层非对象也降级(tmp_path):
    (tmp_path / "finance_data.json").write_text("[1, 2, 3]", encoding="utf-8")
    rt = _rt(tmp_path)
    assert rt.transactions == []
    assert "JSON 对象" in rt.load_error


def test_单条坏记录只跳过自己不废掉整份(tmp_path):
    (tmp_path / "finance_data.json").write_text(json.dumps({
        "version": 1,
        "transactions": [
            {"amount": -10, "category": "food", "entity": "a", "account": "debit"},
            {"category": "food"},
            "not-a-dict",
            {"amount": -20, "category": "food", "entity": "b", "account": "debit"},
        ],
        "budgets": [{"category": "food", "monthly_limit": 300}],
        "account_balances": {"debit": "not-a-number", "cash": 50},
    }, ensure_ascii=False), encoding="utf-8")

    rt = _rt(tmp_path)
    assert [t.entity for t in rt.transactions] == ["a", "b"]
    assert len(rt.budgets) == 1
    assert rt.account_balances == {"cash": 50.0}


def test_实体段缺失时按交易重建(tmp_path):
    (tmp_path / "finance_data.json").write_text(json.dumps({
        "transactions": [
            {"amount": -30, "category": "subscription", "entity": "Netflix",
             "account": "card", "tags": ["recurring"]},
            {"amount": -10, "category": "food", "entity": "Netflix", "account": "card"},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    rt = _rt(tmp_path)
    assert rt.entities["Netflix"].txn_count == 2
    assert rt.entities["Netflix"].total_amount == -40
    assert rt.entities["Netflix"].entity_type == "subscription"


def test_批量导入只写一次盘(tmp_path, monkeypatch):
    rt = _rt(tmp_path)
    calls = []
    orig = rt.save

    def _counting_save():
        calls.append(1)
        return orig()

    monkeypatch.setattr(rt, "save", _counting_save)
    n = rt.import_csv(CSV)
    assert n > 0
    assert len(calls) == 1, f"导入 {n} 笔应只落盘 1 次,实为 {len(calls)} 次"
    assert len(_rt(tmp_path).transactions) == n


def test_落盘失败只回报不抛异常(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("我是个文件,不是目录", encoding="utf-8")
    rt = FinanceRuntime(policy_path=str(tmp_path / "finance_policy.yaml"),
                        data_path=str(blocker / "finance_data.json"))
    assert rt.save() is False
    assert rt.save_error
    rt.add_transaction(Transaction(amount=-1, category="misc",
                                   entity="z", account="debit"))
    assert len(rt.transactions) == 1
    assert rt.status()["persist"]["save_error"]


def test_分析使用持久化后的数据(tmp_path):
    rt = _rt(tmp_path)
    rt.set_budget("food", 100)
    rt.add_transaction(Transaction(amount=-150, category="food",
                                   entity="餐厅", account="debit"))

    report = _rt(tmp_path).analyze(period="2026-11")
    assert report["transaction_count"] == 1
    food = [b for b in report["budget_status"] if b["category"] == "food"]
    assert food and food[0]["is_over"] is True
