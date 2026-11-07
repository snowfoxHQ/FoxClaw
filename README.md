# Foxclaw · Cognitive Agent Operating System

A **cognitive agent OS** built on the HMR memory runtime: three collaborating agents, cross-layer Harness governance, with the memory/cognition layer fully decoupled from any LLM.

```
Traditional frameworks:  memory = storage,           agents = stateless execution
Foxclaw:               memory = living cognitive runtime, agents = reason · learn · evolve
```

## What it is

Foxclaw is not "yet another agent framework" but an operating system for agents. Its moats are independent of any LLM:

- **ThoughtChain** — each agent's reasoning is an auditable, comparable directed graph, not a log
- **Harness** — three-dimensional drift detection (semantic / tool / scope) + shadow-mode self-calibration + policy self-learning
- **Evolution** — every harness truncation teaches the policy; the system gets sharper with use
- **Runtime governance** — idempotency, crash recovery, loop detection, etc., keeping the system reliable under real-world operation

Three agents: Supervisor (coordination), Finance, Office (learning + itinerary). Each has isolated memory and policy evolution, mounts external ecosystems via the Skill layer, and connects to mainstream messaging platforms via the channel layer. The former Assistant and Admin responsibilities are now **instruction skills** (`skills/general-assistant`, `skills/schedule-itinerary`) mounted on demand, not separate agents.

## Domain kernels

Finance / Office each mount an isomorphic kernel (pure-algorithm layer + external tool interface + gatekeeper):

- **Finance** — budget reasoning, MAD anomaly detection, cash-flow forecast; gatekeeper with four permissions, emits approval requests rather than moving money. **Advanced trio**: structured evidence chains (explainable risk), financial data graph (trace who caused an overspend), reconciliation (3-tier matching + suspicious-charge detection).
- **Office** — SM-2 spaced repetition (mastery + forgetting risk) + knowledge graph (cluster discovery).
- **Office · itinerary** — TSP routing, reachability ("won't make it") detection, time conflicts, geo-clustering; integrates Amap.

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
python -m uvicorn foxclaw.api:app --port 8000
```

See [docs/RUN.md](docs/RUN.md). Chinese docs: [README_CN.md](README_CN.md).

> **Windows**: don't place the project under OneDrive-managed folders (Documents/Desktop/Pictures); Python can't create dirs inside redirect placeholders. Use a path like `C:\foxclaw`.

## Knowledge base (HKC) model config

HKC's knowledge base uses **two different types of models**, each for one job, both configured in `.env` — no code changes:

| Stage | Model type | What it does | Config keys |
|-------|-----------|-------------|-------------|
| Write (ingesting books/files) | **Generative LLM** (reads & emits JSON) | Extracts entities / concepts / facts / claims from each chunk | `HKC_LLM_*` |
| Retrieve (querying) | **embedding** (text → vector) | Vectorizes knowledge & queries, ranks by semantic similarity | `HKC_EMBEDDING_*` |

They are not interchangeable: an embedding model cannot generate text (can't produce JSON), and an LLM is not suited to vector search.

### First, pick your case

| Your environment | Extraction model setup | `HKC_LLM_API_KEY` value |
|---|---|---|
| Have local Ollama | `openai-compatible` + `base_url` pointing at Ollama | **any non-empty value** (Ollama ignores it; `ollama` is just a placeholder) |
| No local model | Use a **public cloud model** (DeepSeek / OpenAI / Qwen …), see table | the platform's **real API key** |

### Case A · local Ollama

```bash
HKC_LLM_PROVIDER=openai-compatible
HKC_LLM_API_KEY=ollama                       # placeholder only for local Ollama; never use this for cloud
HKC_LLM_MODEL=my-qwen-32k:latest             # a model name from your `ollama list`
HKC_LLM_BASE_URL=http://localhost:11434/v1   # Ollama's OpenAI-compatible endpoint
HKC_LLM_TIMEOUT=180                          # local models are slow; relax the default 60s
HKC_EXTRACT_CONCURRENCY=2                    # 1–2 for local, up to 5 for cloud APIs
HKC_EXTRACT_BATCH=8                          # optional: extract N chunks per call, saves tokens & time; default 1 = per-chunk
HKC_MAX_CHUNKS_PER_DOC=0                     # optional: per-doc chunk cap; 0 = no cap (ingest whole book), default 500
```

### Case B · public cloud model (no local model)

`HKC_LLM_MODEL` is the platform's **public model id** (passed through verbatim); `HKC_LLM_API_KEY` is the platform's **real key**:

| Platform | `HKC_LLM_PROVIDER` | `HKC_LLM_API_KEY` | `HKC_LLM_MODEL` example | `HKC_LLM_BASE_URL` |
|---|---|---|---|---|
| DeepSeek | `deepseek` | `sk-...` (DeepSeek key) | `deepseek-chat` / `deepseek-reasoner` | leave blank (official default) |
| OpenAI | `openai` | `sk-...` (OpenAI key) | `gpt-4o-mini` / `gpt-4o` | leave blank |
| Qwen (Alibaba) | `openai-compatible` | DashScope key | `qwen-max` / `qwen-plus` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| Zhipu / Moonshot / MiniMax … | `openai-compatible` | that platform's key | that platform's model id | that platform's OpenAI-compatible URL |

DeepSeek example:

```bash
HKC_LLM_PROVIDER=deepseek
HKC_LLM_API_KEY=sk-your-deepseek-key
HKC_LLM_MODEL=deepseek-chat
```

### Retrieval model (embedding)

The retrieval model does **not** use Ollama; it uses sentence-transformers, and auto-downloads weights from HuggingFace when missing locally:

```bash
HKC_EMBEDDING=local
HKC_EMBEDDING_MODEL=BAAI/bge-m3          # auto-downloads on first use (~2.2 GB); or a local path like C:\...\bge-m3
```

Leave it unset if you don't need semantic precision (defaults to `stub` pseudo-semantics, works out of the box).

### Where to put the key, and precedence

- **`.env` is the source of truth** — read at startup; restart after editing.
- The "extraction model" section in the planet page's connection settings is an **optional runtime override** (stored in browser localStorage, may override `.env` on refresh); leave it blank to always follow `.env`.
- The page's first "API Key" field is the backend auth token (`.env`'s `FOXCLAW_API_KEY`), unrelated to the model keys.

> Note: switching the embedding model changes the vector dimension (e.g. 384 → 1024), so you must rebuild the knowledge index (clear and re-ingest).

## Philosophy

Memory and cognition are fully decoupled from the LLM. The model is a replaceable text generator; the real moat (ThoughtChain audit + Harness drift + policy self-learning + domain kernels) exists independently of any model.
