# Changelog

Changes from the first public release 0.1.0 to the current version, newest first.

Chinese version: [CHANGELOG_CN.md](CHANGELOG_CN.md).

## 0.1.3 — 2026-11-11

Bilingual interface, and Settings moved where it belongs.

**Interface**

- New i18n layer (`lib/i18n.js` plus en/zh dictionaries): sidebar, menus, chat, stage, settings and toast strings all resolve through the dictionaries; a missing key falls back to the key, so nothing renders blank.
- Settings gains "Interface → Language" with instant Chinese / English switching, persisted in `localStorage` (`foxclawLang`) and kept across reloads.
- Date and number formatting follow the language (`zh-CN` / `en-US`), and `<html lang>` stays in sync.
- Settings moved out of the "More" popover into a pinned entry at the bottom of the left rail, separated by a rule and left unaffected by session-list scrolling.
- The version badge in the top bar reads `/api/system/info`; it no longer falls back to a hard-coded `v3.0`.

**Docs**

- README reconciled with the repository as it actually is: agent count, channels, ingest caps, chunk geometry, supported formats and internal links all verified one by one, with a test to keep them from drifting again.

## 0.1.2 — 2026-11-10

Version consolidation and skill installation.

- Version number unified across five places (`pyproject.toml` / `foxclaw/__init__.py` / `npm/package.json` / `npm/index.js` / `frontend_src/package.json`), guarded by `tests/test_version_sync.py` so npm and PyPI cannot drift apart.
- `foxclaw skills install` accepts ClawHub / GitHub / URL / local archives; the tools panel can install an instruction skill straight from an uploaded `SKILL.md` or OpenClaw package.
- First run asks how to address you (editable in settings); an optional new-version check was added.
- `tools/sync_public.ps1` now builds the public tree directly instead of relying on a local clone.
- Type annotations closed out: mypy went from 645 to 0, and CI turned into a ratchet that may only go down.
- Explanatory comments stripped repo-wide, so the public tree no longer carries internal notes.

## 0.1.1 — 2026-11-09

npm-only republish; there is no matching version bump in the repository.

- The npm package excludes internal docs (`docs/reports` and friends) and debug leftovers, aborting the pack when any are detected — 0.1.1 had shipped internal documents in the public package.
- `build.cjs` now runs before `npm pack`, fixing GitHub Release assets that were 2 KB empty shells.
- `pyproject` gained a readme, so the PyPI page is no longer description-less.
- `requirements.txt` is compiled with `uv pip compile --universal` so platform-specific dependencies carry markers; CI gained a dependency audit gate.
- MIT license and metadata added.

## 0.1.0 — 2026-11-06

First public release, on both channels: PyPI `foxclaw` and npm `foxclaw`.

- Runtime: FastAPI service plus the `foxclaw` command-line entry point.
- Agents: multiple agents collapsed into three (assistant / office / knowledge); the rest were demoted to instruction skills.
- Knowledge (HKC) and memory (HMR) embedded via `vendor/`: knowledge is extractable and searchable, and review scheduling runs on SM-2.
- Governance: policy gate, audit hash chain, shadow learning and circuit breaker; import paths are allow-listed and skill side effects default to fail-closed.
- Config: typed reads instead of raw `.env`, and `foxclaw config check` turns a misconfiguration from a crash into a message.
- Security: DNS rebinding, browser SSRF and untrusted channel instructions closed off; channel path signatures plus one-shot nonces for sensitive actions.
- Pipeline: visible plans, reasoning emitted as its own SSE event, step-level checkpoints for resume-after-interrupt.
- Frontend: Vue3 three-column renderer, persistent feature entries in the left rail, dual-state inspector on the right.

> Both packages are **placeholders**: `vendor/` (HKC / HMR) is not shipped, so a fresh install only gets the importable skeleton. See [docs/RELEASE.md](docs/RELEASE.md).
