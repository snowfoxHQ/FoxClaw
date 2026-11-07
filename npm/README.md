# foxclaw

A personal agent with memory — a fox living in your device.

The npm package ships the **full project source** under `python/`, the same code
published on PyPI and hosted on GitHub. The `foxclaw` binary forwards its
arguments to the bundled Python CLI.

## Requirements

- Node.js >= 18
- Python >= 3.11 (for running the agent)
- Python dependencies: `pip install fastapi uvicorn pydantic httpx pyyaml`

## Install

```bash
npm install -g foxclaw
```

Or, if you prefer the Python package (recommended, pulls dependencies for you):

```bash
pip install foxclaw
```

## Usage

```bash
foxclaw --help
foxclaw serve --port 8000     # start the HTTP API
foxclaw config                # show resolved config
```

Point at a specific interpreter when several Pythons are installed:

```bash
FOXCLAW_PYTHON=/usr/bin/python3.12 foxclaw serve
```

## Layout

| Path | Contents |
|---|---|
| `python/foxclaw/` | Agent source |
| `python/vendor/hmr/` | Embedded memory runtime |
| `python/vendor/hkc/` | Embedded knowledge core |
| `bin/foxclaw.cjs` | CLI launcher |

## Links

- Homepage: <https://github.com/snowfoxHQ/FoxClaw>
- Python package: <https://pypi.org/project/foxclaw/>
- Issues: <https://github.com/snowfoxHQ/FoxClaw/issues>
