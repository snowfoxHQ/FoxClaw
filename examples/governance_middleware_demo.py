"""
Governance middleware · framework-agnostic integration demo (M3)

Any agent framework (here: a minimal home-grown tool-calling loop) only needs to wrap
one GovernanceSession to gain — three-dimensional drift interception (tool over-reach /
semantic off-goal / scope creep), tool-loop detection, and a fully auditable reason chain.

Fully offline: no Foxclaw is constructed, no agents/skills/channels/HMR are touched,
no network. Content is kept in English so it matches the default English embedding model
(the semantic dimension's sensitivity depends on the embedding matching the language —
see the M1 evals finding; tool/scope enforcement is language-independent).

LangGraph / OpenAI Agents SDK bindings: see docs/GOVERNANCE_MIDDLEWARE.md.
Run: HF_HUB_OFFLINE=1 python examples/governance_middleware_demo.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.governance import GovernanceSession, governed, GovernanceBlocked


def get_balance(account="default"):
    return {"account": account, "balance": 1234.5}


def delete_account(account):
    return {"deleted": account}


def main():
    print("=" * 58)
    print("Governance middleware · framework-agnostic demo")
    print("=" * 58)

    gov = GovernanceSession(
        goal="only read the account balance and transaction history",
        allowed_tools={"read"},
        entities={"balance", "account"},
    )

    guarded_get = governed(gov)(get_balance)
    guarded_delete = governed(gov)(delete_account)

    print("\n[allowed] call read-only tool get_balance:")
    print("   ->", guarded_get(account="savings"))

    print("\n[over-reach] call delete_account (delete not in the read allow-set):")
    try:
        guarded_delete("savings")
    except GovernanceBlocked as e:
        print(f"   x blocked by governance · dim={e.result.dimension} "
              f"tool_score={e.result.scores.get('tool')} reason={e.result.reason}")

    print("\n[observe] observe an off-goal reasoning step:")
    r = gov.observe("write me a romantic poem about the night sky")
    print(f"   -> status={r.status} dim={r.dimension} delta={r.delta:.3f}")

    print("\n[audit] this session's reason chain:")
    for n in gov.audit():
        print(f"   - [{n['harness_status']:<5}] {n['type']:<11} "
              f"{n['content'][:30]:<30} tool={n['tool_name']}")

    print("\n[summary]", gov.summary())
    print("\n" + "=" * 58)
    print("Note: no Foxclaw, no agents/skills/memory were involved — the governance")
    print("core (Harness + ThoughtChain) is a guardrail you can mount on anything.")
    print("=" * 58)


if __name__ == "__main__":
    main()
