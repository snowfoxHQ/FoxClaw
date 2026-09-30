# Hestia OS Run Guide (English)

> Chinese version with more detail: [RUN_CN.md](RUN_CN.md)

## Windows + OneDrive (most common pitfall)

If tests raise `FileNotFoundError: [WinError 2] ... test_data`, the project is under a OneDrive-managed folder (Documents/Desktop/Pictures) whose redirect placeholders Python can't create dirs in. Move it:

```powershell
Copy-Item -Recurse "<your path>\hestia_os" "C:\hestia_os"
cd C:\hestia_os
python tests/run_all.py
```

## 1. Install

```bash
pip install -e .      # dependencies come from pyproject.toml (single source of truth)
# lightweight alternative (uninstalled extras degrade gracefully):
# pip install fastapi uvicorn "httpx<0.28" cryptography websockets pyyaml numpy scikit-learn rich pydantic python-dotenv
```

`cryptography` → Feishu encrypted-event decryption; `websockets` → Discord Gateway; `httpx` → channel HTTP + web tools.

## 2. Verify (no API key needed)

```bash
python tests/run_all.py        # full regression (all green)
python tests/test_e2e.py       # cognitive runtime only
python tests/test_api.py       # API layer
```

These run without an LLM key or real HMR — proving cognition is decoupled from the model.

> **About pytest**: 4 of the 62 tests are pytest-style (`test_tool_use` / `test_injection_guard` /
> `test_sessions` / `test_stats_and_events`, 121 assertions) and need `pip install pytest`
> (or `pip install -e ".[dev]"`). When pytest is absent the runner **explicitly marks those 4 as
> "skipped"** and excludes them from the denominator — they neither vanish silently nor fake a pass.

## 3. Start the service

```bash
export DEEPSEEK_API_KEY=sk-...         # or ANTHROPIC_API_KEY; Windows: $env:...="..."
export HESTIA_PROVIDER=deepseek        # optional explicit provider
export HESTIA_DATA_DIR=./hestia_data
# binds 127.0.0.1 by default. Exposing to LAN requires HESTIA_HOST=0.0.0.0 **and** HESTIA_API_KEY
# (the __main__ entry refuses to start on non-loopback without a key; override with HESTIA_ALLOW_INSECURE=1).
python -m uvicorn hestia.api:app --host 127.0.0.1 --port 8000
```

## 4. Channels & governance

Connect WeChat / Feishu / Telegram / Discord — see [CHANNELS.md](CHANNELS.md). Runtime governance (idempotency / crash recovery / loop detection) is auto-wired — see [GOVERNANCE.md](GOVERNANCE.md).

## 5. Embedded HMR

HMR v2.0 is embedded in `vendor/hmr/`, auto-loaded, persisting to the data dir's `hmr/` subdir. To upgrade, API-compatibly replace `vendor/hmr/`; the adapter (`hestia/core/memory_adapter.py`) and main body stay unchanged.

## 6. Key environment variables

| Var | Meaning |
|-----|---------|
| `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` | LLM key; unset → stub |
| `HESTIA_PROVIDER` | explicit provider |
| `HESTIA_DATA_DIR` | data directory |
| `HESTIA_SHADOW_ROUNDS` | shadow calibration rounds |
| `HESTIA_EMBEDDING_MODEL` | 语义漂移 embedding 模型；中文部署设 `paraphrase-multilingual-MiniLM-L12-v2`（中文跑题硬拦率 0→1.0，见 `evals/governance/compare_embeddings.py`）；默认 `all-MiniLM-L6-v2` |
| `HESTIA_API_KEY` | **Optional auth**: when set, `/api/*` requires `X-API-Key: <value>` (else 401); WebSocket `/ws/harness` uses `?key=<value>` instead (browsers can't set WS headers; the frontend reads it from `localStorage.hestiaApiKey`). When unset, auth is off — safe only on loopback. Frontend and `/docs` stay open. |
| `HESTIA_HOST` / `HESTIA_PORT` | Bind address / port for the `python -m hestia.api` entry. **Default `127.0.0.1:8000`.** |
| `HESTIA_ALLOW_INSECURE` | Set to `1` to let the entry start on a non-loopback host without `HESTIA_API_KEY` (not recommended). |
| `HESTIA_CORS_ORIGINS` | Comma-separated allowed origins. **Default: loopback only** (`localhost` / `127.0.0.1` / `[::1]`, any port). Set explicitly for cross-origin deploys; `*` opens it fully (not recommended). |
| `HESTIA_SHARED_WRITE_MODE` | Shared-memory write policy mode: **`audit` (default) publishes `memory.shared.write` events without blocking**; `warn` also logs; `enforce` actually denies (raises `SharedWriteDenied`). Default is audit because the 8 existing legitimate shared writes must validate the policy table on real traffic before enforcement is switched on. See `hestia/core/shared_write.py`. **Run `python tools/shared_write_audit.py` before rolling forward** — end-to-end acceptance (8 real write sites must pass untouched, 5 intrusion probes must be caught; exit code 0 required). |
| `HESTIA_SHARED_WRITE_AUDIT` | Shared-write audit **persistence** switch (default on): appends every `memory.shared.write` event as a JSON line to `{data_dir}/audit/shared_write.jsonl`, so real-traffic samples accumulate for the enforce decision (the spine history is a 1000-entry ring buffer that high-frequency events flush out). Set `0` to disable. How to judge: review each `allowed=false` line — if all are genuine intrusions and none is a legitimate write, switch to `enforce`. |
| `HESTIA_GOVERNANCE_AUDIT` | Unified **governance audit stream** switch (default on, batch 3): rulings from the Harness/Permission/Orchestration/Skill layers are recorded as `governance.decision` events (WS-visible) and persisted to `{data_dir}/audit/governance.jsonl`. Each record carries a `decision_id` (this ruling) and a `correlation_id` (cross-layer join key: a permission ruling's decision_id is attached to the skill execution audit; negotiation rounds share the req.id). Set `0` to disable (all layers skip automatically, zero overhead). |
| `HESTIA_SKIP_DOTENV` | Set to `1` to **skip loading the project-root `.env` entirely** — process env vars become the only source. **For tests/CI only**: a developer `.env` carrying real keys or `HESTIA_MCP_SERVER=1` / `HESTIA_API_KEY` leaks into test processes, making results machine-dependent, causing tests to hit real external APIs, and writing into real data directories. `tests/run_all.py` sets it to `1` by default. **Do not set this in local development.** |
