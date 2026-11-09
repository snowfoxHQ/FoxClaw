"""
Foxclaw 财务子系统 — 财务数据图 (Financial Data Graph)

借鉴 2026 年企业级财务 Agent 的核心设计:不把推理建在孤立记录上,而是先把
财务数据连接、归一化成一张可审计的关系图,让推理基于"关系"而非"孤立记录"。

适配 Foxclaw 个人/Agent 级:把交易、分类、商户、账户、预算关联成图,支持
关系型查询(这笔超支由谁造成、关联哪些交易、影响哪个账户),与 HMR 关系图呼应。

节点类型:transaction / category / entity(商户) / account / budget
边:交易→分类、交易→商户、交易→账户、预算→分类(预算监管该分类)
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class GraphNode:
    node_id: str
    node_type: str                 # transaction/category/entity/account/budget
    label: str
    attrs: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.node_id, "type": self.node_type,
                "label": self.label, "attrs": self.attrs}


@dataclass
class GraphEdge:
    src: str
    dst: str
    rel: str                       # belongs_to/paid_to/charged_on/monitors
    attrs: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"src": self.src, "dst": self.dst, "rel": self.rel,
                "attrs": self.attrs}


class FinancialDataGraph:
    """财务数据图:把散落的财务数据关联成可查询的图。"""

    def __init__(self):
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self._adj: dict[str, list] = defaultdict(list)        # 出边邻接
        self._radj: dict[str, list] = defaultdict(list)       # 入边邻接

    # ── 构建 ──
    def _ensure_node(self, node_id: str, node_type: str, label: str, **attrs):
        if node_id not in self.nodes:
            self.nodes[node_id] = GraphNode(node_id, node_type, label, dict(attrs))
        else:
            # 已存在 → 累加统计类属性
            self.nodes[node_id].attrs.update(attrs)
        return self.nodes[node_id]

    def _add_edge(self, src: str, dst: str, rel: str, **attrs):
        e = GraphEdge(src, dst, rel, dict(attrs))
        self.edges.append(e)
        self._adj[src].append(e)
        self._radj[dst].append(e)

    def build(self, transactions: list, budgets: list | None = None):
        """从交易 + 预算构建图。"""
        self.nodes.clear(); self.edges.clear()
        self._adj.clear(); self._radj.clear()

        for t in transactions:
            tid = f"txn:{t.id}"
            self._ensure_node(tid, "transaction", f"{t.entity} ¥{abs(t.amount):.0f}",
                              amount=t.amount, timestamp=t.timestamp,
                              is_expense=t.is_expense)
            # 分类节点 + 边(消费统计只累加支出,收入/退款不计入'花费')
            cat_id = f"cat:{t.category}"
            cnode = self._ensure_node(cat_id, "category", t.category)
            if t.is_expense:
                cnode.attrs["total"] = cnode.attrs.get("total", 0) + abs(t.amount)
                cnode.attrs["count"] = cnode.attrs.get("count", 0) + 1
            else:
                cnode.attrs["income"] = cnode.attrs.get("income", 0) + abs(t.amount)
            self._add_edge(tid, cat_id, "belongs_to")
            # 商户节点 + 边(同样只把支出计入消费)
            ent_id = f"ent:{t.entity}"
            enode = self._ensure_node(ent_id, "entity", t.entity)
            if t.is_expense:
                enode.attrs["total"] = enode.attrs.get("total", 0) + abs(t.amount)
                enode.attrs["count"] = enode.attrs.get("count", 0) + 1
            self._add_edge(tid, ent_id, "paid_to")
            # 账户节点 + 边
            acc_id = f"acc:{t.account}"
            self._ensure_node(acc_id, "account", t.account)
            self._add_edge(tid, acc_id, "charged_on")

        # 预算节点 + 监管边(预算 monitors 分类)
        for b in (budgets or []):
            bid = f"bud:{b.category}"
            self._ensure_node(bid, "budget", f"{b.category}预算",
                              monthly_limit=b.monthly_limit, spent=b.spent)
            cat_id = f"cat:{b.category}"
            if cat_id in self.nodes:
                self._add_edge(bid, cat_id, "monitors")
        return self

    # ── 查询 ──
    def neighbors(self, node_id: str, rel: str | None = None) -> list:
        """出边邻居(可按关系过滤)。"""
        return [self.nodes[e.dst] for e in self._adj.get(node_id, [])
                if rel is None or e.rel == rel]

    def transactions_of(self, node_id: str) -> list:
        """某节点(分类/商户/账户)关联的所有交易。"""
        return [self.nodes[e.src] for e in self._radj.get(node_id, [])
                if self.nodes[e.src].node_type == "transaction"]

    def top_entities_in_category(self, category: str, top: int = 3) -> list:
        """某分类下,按消费额排名的商户 —— 回答'这个分类的钱花在谁身上'。"""
        cat_id = f"cat:{category}"
        txns = self.transactions_of(cat_id)
        by_entity: defaultdict[str, float] = defaultdict(float)
        for t in txns:
            if not t.attrs.get("is_expense"):
                continue   # 只统计支出,排除收入/退款
            # 找这笔交易付给了谁
            for e in self._adj.get(t.node_id, []):
                if e.rel == "paid_to":
                    by_entity[self.nodes[e.dst].label] += abs(t.attrs.get("amount", 0))
        ranked = sorted(by_entity.items(), key=lambda x: -x[1])[:top]
        return [{"entity": name, "amount": round(amt, 2)} for name, amt in ranked]

    def trace_overspend(self, category: str) -> dict:
        """追溯某分类超支的成因 —— 图的核心价值:回答'谁造成了超支'。"""
        cat_id = f"cat:{category}"
        bud_id = f"bud:{category}"
        if cat_id not in self.nodes:
            return {"category": category, "found": False}
        cat = self.nodes[cat_id]
        spent = cat.attrs.get("total", 0)
        limit = self.nodes[bud_id].attrs.get("monthly_limit") if bud_id in self.nodes else None
        # 主要贡献者(商户)
        contributors = self.top_entities_in_category(category, top=3)
        result = {
            "category": category, "found": True,
            "total_spent": round(spent, 2),
            "budget_limit": limit,
            "is_over": (limit is not None and spent > limit),
            "top_contributors": contributors,
            "transaction_count": cat.attrs.get("count", 0),
        }
        if contributors:
            top = contributors[0]
            result["primary_driver"] = (
                f"{top['entity']} 贡献了 ¥{top['amount']:.0f}"
                f"({top['amount']/spent*100:.0f}%)" if spent else top["entity"])
        return result

    def account_exposure(self) -> list:
        """各账户的支出敞口 —— 回答'钱主要从哪个账户流出'。"""
        out = []
        for nid, node in self.nodes.items():
            if node.node_type != "account":
                continue
            txns = self.transactions_of(nid)
            total = sum(abs(t.attrs.get("amount", 0)) for t in txns
                        if t.attrs.get("is_expense"))
            out.append({"account": node.label, "outflow": round(total, 2),
                        "transaction_count": len(txns)})
        return sorted(out, key=lambda x: -x["outflow"])

    def stats(self) -> dict:
        by_type: defaultdict[str, int] = defaultdict(int)
        for n in self.nodes.values():
            by_type[n.node_type] += 1
        return {"nodes": len(self.nodes), "edges": len(self.edges),
                "by_type": dict(by_type)}

    def to_dict(self, max_nodes: int = 200) -> dict:
        """导出图(供前端可视化或调试)。"""
        nodes = list(self.nodes.values())[:max_nodes]
        node_ids = {n.node_id for n in nodes}
        return {
            "nodes": [n.to_dict() for n in nodes],
            "edges": [e.to_dict() for e in self.edges
                      if e.src in node_ids and e.dst in node_ids],
            "stats": self.stats(),
        }
