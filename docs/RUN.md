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
pip install fastapi uvicorn "httpx<0.28" openai cryptography websockets pyyaml numpy scikit-learn rich pydantic
# optional, more precise semantic detection (~800MB; falls back to n-gram if absent):
pip install sentence-transformers
```

`cryptography` → Feishu encrypted-event decryption; `websockets` → Discord Gateway; `httpx` → channel HTTP + web tools.

## 2. Verify (no API key needed)

```bash
python tests/run_all.py        # full regression, expect 35/35
python tests/test_e2e.py       # cognitive runtime only
python tests/test_api.py       # API layer
```

These run without an LLM key or real HMR — proving cognition is decoupled from the model.

## 3. Start the service

```bash
export DEEPSEEK_API_KEY=sk-...         # or ANTHROPIC_API_KEY; Windows: $env:...="..."
export HESTIA_PROVIDER=deepseek        # optional explicit provider
export HESTIA_DATA_DIR=./hestia_data
python -m uvicorn hestia.api:app --host 0.0.0.0 --port 8000
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
| `HESTIA_API_KEY` | **可选鉴权**：设了则 `/api/*` 需带 `X-API-Key: <该值>`（否则 401）；WebSocket `/ws/harness` 因浏览器无法设 WS 头，改用 `?key=<该值>`（前端自动带，从 `localStorage.hestiaApiKey` 读）。不设=开放（本地默认）。前端与 `/docs` 始终开放。 |
| `HESTIA_CORS_ORIGINS` | 逗号分隔的允许来源；默认 `*`。部署时收紧到你的前端域名。 |
