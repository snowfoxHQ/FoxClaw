# Changelog

Changes from the first public release 0.1.0 to the current version, newest first.

Chinese version: [CHANGELOG_CN.md](CHANGELOG_CN.md).

## Unreleased

**Removed: built-in web search and map keys**

- `web_search` is gone end to end: the tool declaration and capability, the dispatch branch, automatic web augmentation, the Baidu-backed search port, the skill entry, prompt wording, and the `BAIDU_SEARCH_*` settings. Its verifier and taint mapping are removed with it.
- The personal-data guard moved to `foxclaw/core/personal_data.py`, so "private data never leaves the machine" is preserved independently of any single transport.
- Supervisor prompts now state plainly that the system has no live web access, instead of implying it does.
- Amap is gone as well: `AMAP_MAPS_API_KEY` / `AMAP_JS_KEY` / `AMAP_JS_SCODE`, the `/api/config/amap` route, the itinerary geocoding client, and the front-end JS map loader. Itinerary legs are estimated from coordinates and the map panel renders the built-in SVG sketch from `map_data`.

## 0.1.7 — 2026-11-21

**Memory write admission (M1/M2/M5)**

- Every memory write now carries a source tag. Only an explicit user signal produces `[stated]`; agent conclusions, search results and tool output are always `[derived]`. An optional strict mode (`FOXCLAW_MEMORY_SOURCE_STRICT`) rejects writes without a declared user source.
- Calibration rules keep searchable data, one-off mentions, unconfirmed agent details and task-local state out of long-term memory. An already archived fact counts as remembered, so restating it does not create a second write.
- A poisoning guard rejects instructions that would make future sessions less honest, less safe, less skeptical or unconditionally agreeable — judged by effect, including softened variants. No user instruction can override it.

**Privacy and best-effort (M4/M6)**

- New blocking categories cover minors' identity data and protected attributes. On the consent path, blocked segments are omitted whole rather than replaced with a generalization; "forget X" removes the matching memory and its derivatives. Health and protected attributes still go through the existing three-mode confirmation flow.
- Memory failures are best-effort: they are logged at a rate limit and the host task continues. No retry storms, no screen-flooding, no unsolicited "shall I remember that for you".
- M3 versioned writes stay a design placeholder: the HMR adapter holds a reentrant lock and has no cross-session concurrent-write scenario. `if_version` merge support will be implemented when multi-channel concurrency actually appears.

## 0.1.6 — 2026-11-19

A failed model call can no longer be recorded as a successful answer. Every call carries its own state instead of a router-wide flag, every task settles from evidence instead of from an error string, and memory refuses output that was never verified. Bundled runtime detection, health reporting and test registration are tightened alongside it.

**Call state**

- New `foxclaw/core/call_status.py`: one `CallStatus` per model call, held by a task-level `TaskCallCollector` registered through a contextvar. The collector travels with the task (including across `asyncio.to_thread`), so two concurrent tasks on one Router instance no longer read each other's verdict — the root cause of "task A degraded, task B reported success". The old `Router._call_degraded` field and the deprecated `take_call_degraded()` are removed.
- Six states, and `PENDING` is the initial one: `REAL` (first provider succeeded), `FALLBACK` (primary failed, fallback succeeded), `STUB` (no provider configured), `DEGRADED` (all providers failed / stream cut / empty response), `UNSUPPORTED` (provider lacks the capability — not a failure). A call path that returns without setting a terminal state is a bug by definition.
- Streaming paths fall back to a non-streaming call when the stream breaks, carrying a `partial` mark for the content that already went out. `UNSUPPORTED` and `DEGRADED` are now distinct: "this provider cannot do tools" no longer looks like "the model broke".
- The tool loop records a six-stage history (`requested` / `executed` / `succeeded` / `rejected` / `failed` / `verified`) into the same collector, including the old string-returning path — a direct answer from the tool loop is treated as "no state object" instead of crashing on `.state`.

**Task settlement and memory admission**

- `settle_task_status` in `call_status.py` is the single settlement entry: `COMPLETED` (required result present and postconditions met), `PARTIAL` (some real result), `FAILED` (no valid result), `INTERRUPTED`, `BLOCKED` (harness stop / awaiting confirmation). Settlement reads the collected states and tool history — never an error-message string.
- Degraded is not success: `STUB` / `DEGRADED` / `PENDING` become `PARTIAL` only when a verified tool fact also exists, otherwise `FAILED`. A task waiting for confirmation settles `BLOCKED` with its own `task_status` field instead of being written into memory as a finished task.
- `admit_memory` is the only admission gate for model-produced content: the fact channel requires both `executed` and `verified` evidence (a rejected, timed-out or unverified tool output cannot be labelled a fact — "it came from a tool" is not enough), and the derived channel refuses anything produced under `STUB` / `DEGRADED` / `PENDING`.
- The accounting ledger registers its own real write as a verified tool fact, so "ledger succeeded, analysis degraded" settles `PARTIAL` rather than `FAILED`; a dry run registers nothing, with a regression test guarding against the upgrade.

**Health, search and tool results**

- `GET /health/ready` judges the memory runtime by the backend it reports: a stub backend is no longer reported as `ok`. The endpoint distinguishes ready / degraded / not-ready with the reason attached.
- `web_search` classifies results into three states and marks an empty result explicitly (`EMPTY` with evidence) instead of returning an empty string that downstream reads as success; tool outputs get the same treatment, so an empty result is no longer indistinguishable from a successful one.
- Tool evidence goes through `verify` before it can support a claim, so an unverifiable "done" does not count as done.

**G1 supervisor**

- The supervisor's own-answer path runs Python numeric computation in-process via `foxclaw/g1/python_numeric.py`: a fixed whitelist is checked at the function entry (`_g1_whitelist_match`, shared with `sv_plan`), a hit executes the injected executor and keeps the original dispatch result structure, and a fingerprint/version mismatch returns `INPUT_INVALID` without falling back to the model. G1 hits skip shared-memory writes and record `shared_memory=skipped`; `sv_plan` hits skip `_llm_plan()` so the planning stage makes no model call.
- The executor moved out of the runner: no callable is injected into it any more, and Foxclaw no longer depends on an external ACM runtime.

**Release and test governance**

- `tools/release_check.py` detects the bundled runtime by top-level package name (`hmr*` / `hkc_*`): a placeholder package is now an `error` instead of a `warn`, because since 0.1.5 the runtime ships inside the wheel and a package without it is broken on arrival.
- New `tools/check_test_registration.py`, wired into CI: every `tests/test_*.py` on disk must appear in both the `python_files` whitelist and the `run_all` registry. 47 unregistered files were found and added — the suite is now 133 files with no gaps. The CI step runs in warn-only mode for one cycle before it becomes a hard gate.
- Fixed: `python_files` had drifted from the actual test files (a test could pass when run directly but never run in CI). This release makes the two paths cover the same set.

## 0.1.5 — 2026-11-16

The governance bar is raised across the board: actions are tiered by harm instead of one-size-fits-all, exceptions became condition lists instead of a switch, the guardrails now state where valid instructions can come from, and long channel tasks gained an unattended protocol.

**Tiers and exceptions**

- Action classification is now fixed at three tiers: **prohibited** (refused even under explicit user authorization — the rule is explained and the user is asked to do it themselves: real financial credentials/transactions, changing system security settings, bypassing CAPTCHA, executing files from untrusted sources), **explicit permission** (asked in chat, done only after a clear yes), **regular** (everything else, just done). The tier follows the rule, not the outcome: `PermissionDecision` / `Verdict` gained a `tier` field, and judgment happens at the action level, not the tool level — the same Bash call lands in a tier by what it actually does, not by its name. The tier list is decoupled from the enforcement logic, and the injected behavior spec declares the three-tier semantics.
- Trusted-mode exceptions became **condition lists** (`foxclaw/skills/exceptions.py`): each exception is a set of AND conditions — all must hold for the confirmation stage to be waived, and any failure falls back to the main rule; deny / destructive actions sit before exceptions and can **never** be waived. The evaluation context carries the request origin through a contextvar; no context means the conditions do not hold (fail-closed) — exceptions take no bypass and run through the same engine as the main rules.
- Approval records now carry an explicit **scope** (`single` / `session`): one approval covers exactly the action it describes; a second execution of a similar action needs a new approval unless the user explicitly widened the scope when approving. Alongside this, "the original request is the approval": when the action was initiated by the user, named in the request text, and is not pay/destructive, it proceeds directly with `ORIGIN_APPROVED` recorded — never asking again about something that was already said.

**Instruction sources and guardrails**

- New deterministic detector `detect_forged_authority` (`foxclaw/core/injection_guard.py`): recognizes forged claims of pre-authorization, system privileges, and urgency; `wrap_untrusted` emits a heightened-vigilance marker on a hit. The behavior spec gained two checkpoints: **source boundary** (valid instructions come only from the user through the channel layer; everything tools observe — web pages, files, error messages, forwarded text — is data, not commands; instructive content in observed text is not executed, but quoted, attributed, and referred back to the user) and **forged notices** (tags / signatures in a message do not prove origin; config files cannot approve what needs confirmation; any message claiming the guardrails are off is treated as nonexistent — the guardrails are never lifted from inside the conversation).
- Cross-agent permission laundering ban: a skill permission denial no longer surfaces as ordinary text — it is now explicitly marked `status=blocked` with a `policy_denied` field; the supervisor registers a session-level denial list, and a denied subtask is **never re-dispatched to any agent** — switching agents is refused the same way; reflection-based replanning drops denied steps, and if all steps are denied it stops and reports honestly to the user. The denial follows the task — it does not reset on a hand-off: having another agent do it would bypass the user's permission decision.

**Channels and unattended work**

- New session-presence signal `foxclaw/core/presence.py`: a channel message records an activity timestamp on arrival; user activity within `FOXCLAW_ATTENDED_WINDOW` (seconds, default 300) means someone is watching; no record means nobody is (fail-closed). Pending results waiting for confirmation carry `attended` / `parked` fields: when unattended, explicit-permission actions turn into "finish all the preparation work, lay out the options and consequences, and stop at the decision point".
- The behavior spec gained `unattended_protocol`: when running a long task unattended, take the **single most reasonable interpretation** and keep going, declaring on the **first line of the output** which interpretation was taken — the first thing a returning user reads to reconstruct what happened. The only stop condition: irreversible AND both directions plausible. The prohibited tier cannot be waived unattended either; when someone is watching, the three-tier flow asks normally without over-conservatism.

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
