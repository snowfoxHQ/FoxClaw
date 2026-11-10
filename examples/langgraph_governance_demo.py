"""
治理中间件 · 真实 LangGraph 接入 demo（M3 生态收尾）

用真实的 LangGraph `StateGraph`(带循环 + 条件边)跑一个工具执行图,每一步工具调用都经
Foxclaw 的 `GovernanceSession` 网关:越权工具在**真实 LangGraph 执行流里**被拦,图优雅停机,
事后 `gov.audit()` 取回完整可审计推理链。

这把 M3 从"能挂"变成"挂在真框架上给你看"。离线可跑(工具是 stub,不需 LLM)。
依赖:pip install langgraph   用法:python examples/langgraph_governance_demo.py
"""
import sys
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).parent.parent))

from langgraph.graph import StateGraph, END
from foxclaw.governance import GovernanceSession


class AgentState(TypedDict):
    plan: list
    idx: int
    outputs: list
    halted: bool
    reason: str


def get_balance(account="default"):
    return {"account": account, "balance": 1234.5}

def list_transactions(account="default"):
    return {"account": account, "count": 12}

def delete_account(account="default"):
    return {"deleted": account}

TOOLS = {"get_balance": get_balance, "list_transactions": list_transactions,
         "delete_account": delete_account}


def build_graph(gov: GovernanceSession):
    """构建带治理网关的 LangGraph:每步工具调用先过 Harness,越权则停机。"""

    def step(state: AgentState) -> dict:
        i = state["idx"]
        tool, args = state["plan"][i]
        r = gov.observe(f"tool call: {tool}", tool=tool, semantic=False)
        if r.tool_violation or r.scope_violation or r.blocked:
            return {"halted": True, "reason": r.reason, "idx": i}
        out = TOOLS[tool](**args)
        return {"idx": i + 1,
                "outputs": state["outputs"] + [{"tool": tool, "out": out}]}

    def route(state: AgentState) -> str:
        if state.get("halted") or state["idx"] >= len(state["plan"]):
            return "end"
        return "continue"

    g = StateGraph(AgentState)
    g.add_node("step", step)
    g.set_entry_point("step")
    g.add_conditional_edges("step", route, {"continue": "step", "end": END})
    return g.compile()


def main():
    print("=" * 60)
    print("治理中间件 · 真实 LangGraph 接入 demo")
    print("=" * 60)

    gov = GovernanceSession(
        goal="read the account balance and transactions only",
        allowed_tools={"read"}, entities={"balance", "account"})

    graph = build_graph(gov)
    plan = [("get_balance", {}), ("list_transactions", {}),
            ("delete_account", {}), ("get_balance", {})]
    final = graph.invoke(
        {"plan": plan, "idx": 0, "outputs": [], "halted": False, "reason": ""},
        config={"recursion_limit": 50})

    print(f"\n计划工具序列: {[t for t, _ in plan]}")
    print(f"实际执行到第 {final['idx']} 步就停机 · halted={final['halted']}")
    print(f"停机原因: {final['reason']}")
    print(f"已成功执行: {[o['tool'] for o in final['outputs']]}")

    print("\n[审计] LangGraph 执行过程的可审计推理链:")
    for n in gov.audit():
        print(f"   - [{n['harness_status']:<5}] {n['content'][:24]:<24} tool={n['tool_name']}")

    ok = (final["halted"] and final["idx"] == 2
          and [o["tool"] for o in final["outputs"]] == ["get_balance", "list_transactions"]
          and any(n["harness_status"] == "BLOCK" and n["tool_name"] == "delete_account"
                  for n in gov.audit()))
    print("\n" + "=" * 60)
    print("结论:", "✓ 治理中间件在真实 LangGraph 执行流中拦截越权 + 留下审计"
          if ok else "✗ 未达预期")
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
