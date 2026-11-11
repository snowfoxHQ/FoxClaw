# Changelog

Changes from the first public release 0.1.0 to the current version, newest first.

Chinese version: [CHANGELOG_CN.md](CHANGELOG_CN.md).

## 0.1.4 — 2026-11-12

A conflict card can now be dismissed instead of decided, the validator stops reading every Chinese 不 as a negation, each agent gets its own rules file, and the hard boundaries now live in one checkable spec instead of being scattered across prompts.

**Behavior**

- `foxclaw/behavior/behavior.yaml` is a checkpoint list, not a prose spec: every rule that claims code enforcement names a callable that already exists (`foxclaw.kernel.policy_gate:PolicyGate.check_action`, `foxclaw.core.confirm_nonce:ConfirmationBroker.consume`, `foxclaw.core.verify:verify`), and the spec is resolved at load time. A missing target fails startup instead of registering an always-allow stub, so the list cannot drift into decoration.
- The block is injected first in the system prompt — ahead of the kernel prompt, the role prompt and the per-agent rules. Persona stays where it was (preferences merged into the last user message), so a user-editable preference cannot structurally override a hard boundary.
- `behavior.local.yaml` in `FOXCLAW_DATA_DIR` can add rules and tighten `enforce`, but weakening or dropping a rule in `prohibitions` / `confirmations` raises. A malformed or stale-version local file is ignored with a warning rather than bricking the app; problems in the built-in file stay fatal.
- Rules whose code side is not built yet (`no_privacy_upload`, `no_impersonation`) are marked `enforce: prompt` with a `note` saying what is missing, rather than pretending to be enforced.
- Set `FOXCLAW_BEHAVIOR=0` to turn the whole block off.

**Memory**

- High-sensitivity writes to long-term memory now pass through a deterministic gate: credential / ID-number / health / card-number patterns plus a Luhn check, no model call. `FOXCLAW_SENSITIVE_WRITE_MODE` has three levels — `audit` (default, records only), `warn`, and `enforce`. In `enforce` the content is not stored; it becomes a pending item the user must approve, and `approve_sensitive_write` is the only caller allowed to pass `consent=True`.
- The classifier deliberately over-blocks: a false positive costs one confirmation, a false negative means the rule does not exist. Ordinary amounts and order numbers are excluded on purpose — otherwise the finance agent would be unusable.
- `GET /api/memory/pending`, `POST /api/memory/pending/{id}/approve` and `/reject` expose the queue. The memory panel lists what is waiting, with 记住 / 丢弃 buttons, and when a write is held back the agent says so in the thought chain instead of silently dropping it.
- `confirm_high_sensitivity` is now `enforce: both` in the behavior spec; the checkpoint resolves to the real gate.

**Content safety**

- New gate at `foxclaw/core/content_guard.py`: deterministic, no model call. Both the user request and the model output are checked, and a hit is cut off with a fixed line ("Let's talk about something else." / 「我们换个话题聊聊吧。」) — no explanation, no elaboration, no counter-question, no alternative offered. A refusal must not become a channel for leaking information or continuing the thread.
- Built-in rules cover **solicitation only** (making drugs or explosives, fraud and money laundering, breaking into someone's systems, forging documents, suicide methods, sexual content involving minors). Narrow wording, low false-positive rate, works out of the box. Mentioning is not asking: a book about the history of explosives, a paper on money-laundering sentencing, an order number, `born in '91` all pass.
- The platform-review categories (political, sexual, violent) run to tens of thousands of terms; hard-coding them is neither practical nor maintainable as the rules change. Deployers mount an external lexicon through `FOXCLAW_CONTENT_LEXICON` (comma-separated files, one term per line, optional `term<TAB>category`); `tools/build_content_lexicon.py` does the conversion (encoding detection, category filtering, category inferred from the file name, read-back verification), and which categories to mount is their call. **Nothing is checked against it unless it is mounted** — by default only the built-in rules run.
- `FOXCLAW_CONTENT_GUARD_MODE` has three levels: `enforce` (default), `audit` (records only), `off`. Enforcement is the default because this layer exists to backstop a shipped product; a default that does not fire is not a backstop. The built-in rules are narrow enough not to interrupt ordinary conversation.
- Normalization defeats full-width characters, zero-width characters and inserted spaces. Pinyin, homophones and character-splitting are explicitly out of scope for a deterministic layer — this is a speed bump, not a wall.
- Knowledge-base ingestion and attachment bodies are **not** filtered: that is the user's own material. The gate sits on the request and the output only, so a history book or a legal statute is not mistaken for a violation.
- New `no_harmful_content` rule in the behavior spec (`enforce: both`), and a new §8 "content boundaries" in `KERNEL_PROMPT` spelling out the refusal pattern and the difference between 「material」 and 「instructions」.
- `docs/CONTENT_GUARD.md` / `docs/CONTENT_GUARD_CN.md` document mounting, lexicon format, conversion and false-positive tuning. No political, sexual or violent list is bundled; mounting one, and which mode to run, is the deployer's decision.

**Agents**

- An agent now loads `foxclaw/agents/rules/<AGENT_ID>/*.md` into its system prompt when it starts. The files are re-read on every prompt assembly, so editing rules needs no code change; the block is appended after the role prompt and yields to the safety rules above it. Set `FOXCLAW_AGENT_RULES=0` to turn it off.
- Ships with `foxclaw/agents/rules/finance/RULES.md` — Personal Finance Agent Rules v1.0 (data truthfulness, bookkeeping accuracy, privacy, calculation conventions, advice limits, anomaly handling, memory boundaries, supervisor collaboration). The file is package data, so it travels inside the wheel instead of being silently dropped.

**Knowledge**

- `POST /conflicts/{id}/dismiss` and `POST /conflicts/bulk-dismiss` (SDK: `dismiss_conflict` / `dismiss_conflicts`) close a card without picking a winner: both claims stay `active` and nothing is marked `superseded`. A claim that was `disputed` only because of that card goes back to `active` once no open conflict mentions it.
- Conflict detection no longer flips polarity on topical similarity alone. The negated side now also has to overlap lexically with the other statement (character-bigram Jaccard ≥ 0.40) and stay close in embedding space (cosine ≥ 0.35); both thresholds are constructor parameters (`neg_flip_min_cos`, `neg_flip_min_overlap`).
- Descriptive `不` / `无` compounds (`不变`, `不同`, `不管`, `不再`, `无需`, `不可观测`, `不可控` …) no longer count as stance negation.
- The conflict card gained a secondary "false positive" button beside "accept selected claim".
- `tools/recheck_conflicts.py` re-scores existing open cards with the current rule and batch-closes those that no longer hold. It dry-runs by default; `--apply` submits through the running service, so the in-process conflict set and the vector index stay in step.

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
