# Running Foxclaw

## Windows and OneDrive paths

If tests on Windows fail with `FileNotFoundError: [WinError 2] ... test_data`, the project is likely under an OneDrive-managed folder (Documents / Desktop / Pictures). These are redirect placeholders in which Python cannot create directories. Move the project to an unmanaged path:

```powershell
Copy-Item -Recurse "<current path>\foxclaw" "C:\foxclaw"
cd C:\foxclaw
python tests/run_all.py
```

## 1. Install dependencies

```bash
pip install -e .                # pyproject.toml is the single source of truth
```

`cryptography` is used for Feishu event decryption, `websockets` for Discord, and `httpx` for channel HTTP calls. If `sentence-transformers` is missing, some semantic capabilities degrade gracefully rather than crashing.

## 2. Verify (no API key needed)

```bash
python tests/run_all.py         # full regression
python tests/test_e2e.py
python tests/test_api.py
```

These tests run without a model key. They use pytest, so install it with `pip install pytest` (or `pip install -e ".[dev]"`).

## 3. Start the service

```bash
export DEEPSEEK_API_KEY=sk-...         # or ANTHROPIC_API_KEY; on Windows: $env:DEEPSEEK_API_KEY="sk-..."
export FOXCLAW_PROVIDER=deepseek        # optional: choose a provider explicitly
export FOXCLAW_DATA_DIR=./foxclaw_data   # data directory

python -m uvicorn foxclaw.api:app --host 127.0.0.1 --port 8000
```

Open http://localhost:8000 .

The service listens on localhost by default. To expose it beyond the machine, set both `FOXCLAW_HOST=0.0.0.0` and `FOXCLAW_API_KEY`. Do not expose a non-loopback address without an access key.

## 4. Connect messaging platforms

WeChat, Feishu, Telegram and Discord are each implemented to their own official spec, with credentials injected locally. See [CHANNELS.md](CHANNELS.md) for obtaining tokens, starting each channel and running several at once.

## 5. Runtime governance

Idempotency, crash recovery and loop detection are wired in automatically — no extra configuration. See [GOVERNANCE.md](GOVERNANCE.md). After a crash, the service prints the tasks that were interrupted on startup.

## 6. Environment variables

| Variable | Description |
|---|---|
| `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` | Model key; without one a stub is used |
| `FOXCLAW_PROVIDER` | Choose a provider explicitly (deepseek / anthropic …) |
| `FOXCLAW_DATA_DIR` | Data directory, default `./foxclaw_data` |
| `FOXCLAW_API_KEY` | Access key: when set, API and knowledge endpoints require `X-API-Key`, WebSocket uses `?key=`. Without it there is no authentication, so keep access local |
| `FOXCLAW_HOST` / `FOXCLAW_PORT` | Bind address and port, default `127.0.0.1:8000` |
| `FOXCLAW_CORS_ORIGINS` | Allowed cross-origin sources; localhost only by default, `*` opens all (not recommended) |
| `FOXCLAW_SKIP_DOTENV` | Set to `1` to skip loading `.env` and use only process variables. For tests/CI; do not set during local development |
