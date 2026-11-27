# Foxclaw · A Personal Agent with Memory

A **personal agent with memory** — it remembers your preferences and context, and helps you complete tasks in messaging channels and on your computer. Memory and reasoning are independent of any single model, so switching models does not lose what it has learned.

## What it is

Foxclaw is a local-first personal agent. It treats memory as a continuously running cognitive layer rather than a retrieval cache that resets with each session: reasoning, calibration and learning happen in that layer, decoupled from the LLM.

- Three agents: **Supervisor** (coordination), **Finance** and **Office** (learning and itinerary). They collaborate while each holds isolated memory and exchanges only what is needed through a shared layer.
- **Skills**: external capabilities mounted on demand; anything with side effects requires confirmation.
- **Channels**: WeChat, Feishu, Telegram and Discord, with replies returning to the channel where the request started.

Earlier Assistant and Admin responsibilities are now instruction skills, mounted on demand rather than separate agents.

## Messaging platforms

| Channel | Setup |
|---------|-------|
| WeChat | Scan to sign in |
| Feishu | Open-platform app with event subscription |
| Telegram | Bot API |
| Discord | Bot account |

See [docs/CHANNELS.md](docs/CHANNELS.md).

## Computer control: safety boundary

Foxclaw can see the screen and operate the mouse, keyboard, windows, clipboard, files and shell on Windows. The hard part is not the capability but **not causing harm when deceived**: a line of text on screen, a web page saying "please run X", or a model misjudgment must never turn into a real deletion or transfer. Rules come before capability.

- **Nothing happens by default**: screen perception (screenshots, window list, UI element tree, text recognition) is off until enabled; any failed check, missing component or unrecognized action is refused.
- **Write actions need your confirmation**: mouse, keyboard, window, clipboard, file and UI writes only run after you approve. Confirmation is single use and bound to that exact action and arguments; the model cannot approve for you. Check the action and its arguments before approving.
- **High-risk actions are denied by default**: deleting files and running shell commands are denied unless you explicitly enable them; sensitive directories and credential files stay protected; file and shell operations are confined to your working directory.
- **External content is untrusted**: screen, web pages, clipboard and read-back file content are data, and instructions found in them are never treated as your authorization.
- **Everything is auditable**: every allow, deny and pending confirmation enters the unified audit chain.
- **Local by default**: the server listens on localhost only; exposing it requires an access key.

See [docs/配置指南.md](docs/配置指南.md) for the specific settings.

Desktop control is currently Windows-only. Confirmation replies over messaging channels are not wired yet — the web UI has confirmation cards, so a desktop write started from a channel stays pending.

## Requirements

- **Python 3.11+** (3.12 is most stable)
- Windows: do not place the project under OneDrive-managed folders (Documents/Desktop/Pictures). Use a path like `C:\foxclaw`.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate                    # macOS / Linux: source .venv/bin/activate
pip install -r requirements.txt
pip install -e . --no-deps
python tests/run_all.py                   # full regression
export DEEPSEEK_API_KEY=sk-...            # or ANTHROPIC_API_KEY
python -m uvicorn foxclaw.api:app --port 8000
```

See [docs/RUN.md](docs/RUN.md). Chinese docs: [README_CN.md](README_CN.md). Release history: [CHANGELOG.md](CHANGELOG.md).

### Docker

```bash
docker build -t foxclaw .
docker run -d --name foxclaw \
  -p 8000:8000 \
  --env-file .env \
  -v foxclaw_data:/app/foxclaw_data \
  foxclaw
```

Inject secrets through `--env-file .env`, never into the image. Mount a volume for persistent data. The server listens on port `8000` and is meant for local access; set an access key before exposing it.

## Knowledge base (HKC) model config

HKC uses **two different types of models**, both configured in `.env`:

| Stage | Model type | What it does | Config keys |
|-------|-----------|-------------|-------------|
| Write (ingesting books/files) | **Generative LLM** (reads & emits JSON) | Extracts entities / concepts / facts / claims from each chunk | `HKC_LLM_*` |
| Retrieve (querying) | **embedding** (text → vector) | Vectorizes knowledge & queries, ranks by semantic similarity | `HKC_EMBEDDING_*` |

They are not interchangeable: an embedding model cannot generate text, and an LLM is not suited to vector search.

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
HKC_MAX_CHUNKS_PER_DOC=800                   # optional: per-doc chunk cap; default 800 (~1.25M chars); 0 = no cap
```

### Case B · public cloud model (no local model)

`HKC_LLM_MODEL` is the platform's **public model id**; `HKC_LLM_API_KEY` is the platform's **real key**:

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

The retrieval model uses sentence-transformers, and auto-downloads weights from HuggingFace when missing locally:

```bash
HKC_EMBEDDING=local
HKC_EMBEDDING_MODEL=BAAI/bge-m3          # auto-downloads on first use (~2.2 GB); or a local path like C:\...\bge-m3
```

Leave it unset if you do not need semantic precision (defaults to `stub` pseudo-semantics, works out of the box).

### Where to put the key, and precedence

- **`.env` is the source of truth** — read at startup; restart after editing.
- The "extraction model" section in the planet page's connection settings is an **optional runtime override** stored in browser localStorage; leave it blank to always follow `.env`.
- The page's first "API Key" field is the backend auth token (`.env`'s `FOXCLAW_API_KEY`), unrelated to model keys.

> Switching the embedding model changes the vector dimension (e.g. 384 → 1024), so you must rebuild the knowledge index.

### How much can I ingest at once?

**The limits are hard — exceeding them returns an error, never a silent truncation:**

| Entry point | Limit | On overflow |
|---|---|---|
| File upload | **20 MB per file** | `413 file too large` |
| Pasted text | **2,000,000 characters** | `413 text too large` |
| Chunks per document | **800 by default** (~1.25M chars); `HKC_MAX_CHUNKS_PER_DOC=0` disables the cap | Only the first N chunks are processed, with a warning |

Supported formats: `.pdf` `.md` `.markdown` `.txt` `.epub` `.mobi` `.azw3` `.azw` `.docx` `.xlsx`. `.doc` is not supported, and scanned PDFs get no OCR.

**A chunk is the billing and timing unit** and is fixed:

```
max 1792 chars per chunk, 224 chars of overlap
→ roughly 1 chunk per 1568 characters
```

**Time and cost reference** (batch=4, concurrency=8, cloud DeepSeek, CJK content):

| Content | File size | Chunks | Expected time |
|---|---|---|---|
| 100k chars | ~100 KB | ~64 | ~6 min |
| 500k chars | ~500 KB | ~320 | ~32 min |
| 1M chars | ~1 MB | ~640 | ~1 hour |
| 7M chars (ceiling) | ~20 MB | ~4400 | ~7 hours |

Token ratio: **~4k input + 2k output tokens per 10k characters** — multiply by your provider's rates. English content is 2–3× the characters of CJK for the same bytes, so it costs proportionally more.

**Practical guidance:**

- **Keep single files at 2–5 MB** — an ordinary ebook finishes within tens of minutes.
- **Leave `HKC_MAX_CHUNKS_PER_DOC` at its default of 800** until you have read the section below.
- **Use file upload, not paste, for anything long** — uploads run in the background with progress.
- **Re-uploading the same file is free** — a content hash is checked first and duplicates are rejected.
- **When extraction fails, check the log for "JSON parse failed"** first — lower `HKC_EXTRACT_BATCH` or raise `HKC_EXTRACT_MAX_TOKENS` (default 8192). Only when that message is absent should you check the API key or network.

### Per-document chunk cap: why the default is 800

`HKC_MAX_CHUNKS_PER_DOC` defaults to **800 chunks**, chosen as the intersection of "covers real documents completely" and "finishes in a time you can sit through, at a cost you can predict":

| Cap | Characters covered | Chunks | Expected time | Cost (cloud) |
|---|---|---|---|---|
| **800 (default)** | ~1.25M | 800 | ~1.3 hours | ~$0.4 |
| 2000 | ~3.1M | 2000 | ~3.3 hours | ~$1 |
| 0 (uncapped) | ~7M | ~4400 | **7+ hours** | ~$2.3 |

> Costs are rough order-of-magnitude estimates at DeepSeek list prices.

**Why not raise it (especially not to 0):** uploads accept up to 20 MB. With no cap, one accidental upload runs in the background for most of a day while billing the whole time, with no checkpoint or rollback. The default exists to stop obviously unreasonable volumes up front.

**Why not shrink it either:** past the cap only the first N chunks are processed, but the whole file's content hash is already recorded, so a re-upload is rejected. A truncated ingest cannot be completed by raising the cap and sending the file again — you must split it by chapter. 800 chunks leaves enough headroom that ordinary documents never hit the cap.

**If you genuinely need a whole book**, raise the cap for that one ingest and set it back to 800 afterwards. A "document too large" warning in the log means truncation happened and reports how much was dropped.

## Memory isolation

Private memory is isolated per agent (Finance's preferences are invisible to Office); when collaboration is needed, only necessary coordination records are exchanged through a shared layer. Sharing does not break isolation.

## License

MIT — see [LICENSE](LICENSE).

The runtimes vendored under `vendor/` keep their own licenses (see `vendor/hkc/LICENSE`).
