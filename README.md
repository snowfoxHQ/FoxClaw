# Hestia OS · Cognitive Agent Operating System

A **cognitive agent OS** built on the HMR memory runtime: five collaborating agents, cross-layer Harness governance, with the memory/cognition layer fully decoupled from any LLM.

```
Traditional frameworks:  memory = storage,           agents = stateless execution
Hestia OS:               memory = living cognitive runtime, agents = reason · learn · evolve
```

## What it is

Hestia OS is not "yet another agent framework" but an operating system for agents. Its moats are independent of any LLM:

- **ThoughtChain** — each agent's reasoning is an auditable, comparable directed graph, not a log
- **Harness** — three-dimensional drift detection (semantic / tool / scope) + shadow-mode self-calibration + policy self-learning
- **Evolution** — every harness truncation teaches the policy; the system gets sharper with use
- **Runtime governance** — idempotency, crash recovery, loop detection, etc., keeping the system reliable under real-world operation

Five agents: Supervisor (coordination), Assistant (general), Finance, Admin (itinerary), Office (learning). Each has isolated memory and policy evolution, mounts external ecosystems via the Skill layer, and connects to mainstream messaging platforms via the channel layer.

## Domain kernels

Finance / Office / Admin each mount an isomorphic kernel (pure-algorithm layer + external tool interface + gatekeeper):

- **Finance** — budget reasoning, MAD anomaly detection, cash-flow forecast; gatekeeper with four permissions, emits approval requests rather than moving money. **Advanced trio**: structured evidence chains (explainable risk), financial data graph (trace who caused an overspend), reconciliation (3-tier matching + suspicious-charge detection).
- **Office** — SM-2 spaced repetition (mastery + forgetting risk) + knowledge graph (cluster discovery).
- **Admin** — TSP routing, reachability ("won't make it") detection, time conflicts, geo-clustering; integrates Amap.

## Messaging platforms

Four mainstream platforms via a unified channel abstraction, **each adapter implemented to its own official spec** (not one mold):

| Channel | Model | Credential |
|---------|-------|-----------|
| WeChat | iLink Bot API, long-poll + context_token | scan-login token |
| Feishu | event-subscription webhook + signature + token refresh | App ID/Secret |
| Telegram | Bot API getUpdates polling | bot token |
| Discord | Gateway WebSocket + REST | bot token |

Closed loop (`ChannelDispatcher`): receive → Supervisor → reply to origin, with per-channel session isolation and idempotent dedup. See [docs/CHANNELS.md](docs/CHANNELS.md).

## Runtime governance

Keeps the system reliable under message redelivery, crashes, long tasks, and loops. See [docs/GOVERNANCE.md](docs/GOVERNANCE.md): idempotency guard, crash/restart recovery (boot_id avoids pid reuse), tool-loop detection, context compaction, hook standardization, steering queue.

## Quick start

```bash
pip install fastapi uvicorn "httpx<0.28" openai cryptography websockets pyyaml numpy scikit-learn rich pydantic
python tests/run_all.py        # full regression (all green)
export DEEPSEEK_API_KEY=sk-...  # or ANTHROPIC_API_KEY; unset → stub
python -m uvicorn hestia.api:app --port 8000
```

See [docs/RUN.md](docs/RUN.md). Chinese docs: [README_CN.md](README_CN.md).

> **Windows**: don't place the project under OneDrive-managed folders (Documents/Desktop/Pictures); Python can't create dirs inside redirect placeholders. Use a path like `C:\hestia_os`.

## Philosophy

Memory and cognition are fully decoupled from the LLM. The model is a replaceable text generator; the real moat (ThoughtChain audit + Harness drift + policy self-learning + domain kernels) exists independently of any model.
