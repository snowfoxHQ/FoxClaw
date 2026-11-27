# Changelog

Changes from the first public release 0.1.0 to the current version, newest first.

Chinese version: [CHANGELOG_CN.md](CHANGELOG_CN.md).

## 0.1.9 — 2026-11-28

**More reliable computer task execution**

- **Unified execution governance**: local and governed execution modes are aligned, with consistent authorization rules across desktop, browser and skill operations. When the governance service is unavailable, high-risk actions do not bypass safety limits.
- **Stronger operation authorization**: improved user confirmation for browser actions, preventing unauthorized execution, confirmation reuse and argument substitution.
- **Task state management**: structured task state that stores the goal, user constraints, progress and artifacts, with recovery after interruption.
- **Finish verification**: an independent check of file artifacts and task postconditions, distinguishing success, failure and undetermined results.
- **Multi-channel computer use**: better coordination between programmatic, semantic and visual interaction, reducing unnecessary screenshots and coordinate-based actions on complex desktop tasks.
- **UI automation**: improved UI Automation batch operations for Windows desktop interaction.

Safety and reliability:

- Consistent permissions across channels.
- Better source tracking for web, screen and external data.
- Improved operation auditing, failure handling and task state records.
- Stronger checks between task execution and the final result.

## 0.1.8 — 2026-11-27

**Computer use arrives; privacy boundary tightened**

- Built-in web search and map capabilities removed: no more live web search or map service; itinerary distances are estimated locally and the map panel uses a built-in schematic.
- Personal-data protection no longer depends on a single transport and applies consistently across channels.
- Windows computer use: screen perception (screenshots, window list, UI element tree, text recognition) and desktop operations (mouse, keyboard, windows, clipboard, files, shell and UI interactions). All operations are authorized, write actions require user confirmation, and unconfirmed actions never run.
- Browser operations: page perception plus navigate, click, type, scroll, extract and wait; write actions require confirmation as well.
- High-risk protection: deleting files and running shell commands are denied by default and must be explicitly enabled by the user; sensitive directories and credential files stay protected; file and shell operations are confined to the working directory.
- Perception and operation are authorized separately; screen and web content is treated as untrusted, so instructions found on a page are never treated as user authorization; every authorization decision is recorded in the unified audit chain.
- Desktop control is currently Windows-only.

## 0.1.7 — 2026-11-21

**Better memory quality and privacy**

- Memory provenance: only content the user explicitly states is recorded as a user statement; assistant conclusions, search results and tool output are labelled separately and never mixed in as user facts.
- Tighter long-term memory admission: one-off mentions, unconfirmed details and task-local state no longer enter long-term memory; restating a fact does not create a duplicate.
- Poisoning protection: instructions that would weaken honesty, safety or independent judgement are rejected, including softened variants, and cannot be overridden by user instruction.
- Expanded privacy blocking: new categories for minors' identity data and protected attributes; blocked content is omitted rather than stored, and "forget X" removes the matching memory and its derivatives.
- Memory write failures no longer interrupt the main task or trigger retries and unsolicited prompts.

## 0.1.6 — 2026-11-19

**Truthful results and task settlement**

- Model calls are recorded accurately: real success, fallback success, no model configured, degraded failure and unsupported capability are distinguished; a failed call is never logged as a success, and concurrent tasks keep their call states isolated.
- Interrupted streaming falls back to a non-streaming call and marks the content already emitted.
- Task settlement is evidence-based: the final state is derived from actual results and postconditions, a degraded call is never a complete success, and a task awaiting confirmation is no longer treated as finished.
- Tighter memory admission: only verified tool results can become facts in long-term memory; unverified or failed output is not stored.
- Health checks distinguish ready, degraded and not-ready, instead of reporting a placeholder runtime as healthy.
- Empty results are no longer confused with successful ones, and tool output must be verified before it can support a claim.
- The supervisor's numeric computation runs in-process, reducing dependence on model behaviour.
- Release checks fail when the bundled runtime is missing; test registration is complete, so a test cannot pass locally yet never run in CI.

## 0.1.5 — 2026-11-16

**Finer governance rules and unattended operation**

- Action tiers: prohibited, explicit permission required, and regular. Classification follows what an action actually does, not the name of the tool.
- Tighter exceptions: exceptions changed from a switch to condition lists that must all hold before a confirmation is waived; prohibited and destructive actions can never be waived.
- Approval scope: one approval covers exactly the action it describes; actions explicitly named in the user's original request are not asked about again.
- Instruction-source boundary: only instructions from the user through a messaging channel are followed; web pages, files, error messages and other observed content are data, not commands; forged authorization claims and manufactured urgency no longer work.
- Cross-agent boundary: a denied action is not handed to another agent to retry, and the denial follows the task rather than resetting on a hand-off.
- Unattended long tasks: with nobody present, the most reasonable single interpretation is used and declared on the first line of the output; the task stops and waits when an action is irreversible and both directions are plausible.

## 0.1.4 — 2026-11-12

**Behaviour, memory safety and knowledge conflicts**

- Centralized behaviour specification: hard boundaries live in one verifiable list whose rules must point to implemented capabilities; user preferences cannot override them, and local configuration can only tighten them.
- Sensitive memory confirmation: high-sensitivity content (credentials, ID numbers, health, card numbers) is checked before entering long-term memory; in strict mode it is not stored until the user approves. The check deliberately errs on the side of blocking.
- Memory confirmation panel: a queue of pending writes that the user can approve or discard in place.
- Agent rules: each agent can load its own rules file; editing rules needs no code change, and safety rules take precedence on conflict.
- Content safety: a deterministic gate checks both user requests and model output, stopping with a fixed message and no elaboration. Built-in rules cover only explicit solicitation of illegal or harmful content; the user's own documents and attachments are not filtered. Deployers may mount an external lexicon.
- Knowledge conflicts: a conflict card can be marked as a false positive instead of forcing a choice; negation is no longer inferred from the mere presence of a negative word; batch re-checking of stored conflicts was added.

## 0.1.3 — 2026-11-11

**Bilingual interface**

- The interface switches instantly between Chinese and English, with date and number formats following the language and persisting across reloads.
- Settings moved to a pinned entry at the bottom of the left rail.
- The version badge reads the server's version instead of a hard-coded one.
- README reconciled with the repository and covered by a drift test.

## 0.1.2 — 2026-11-10

**Version consolidation and skill installation**

- Version numbers unified across channels to prevent npm and PyPI drift.
- Skill installation supports ClawHub, GitHub, URLs and local packages; the tools panel can install directly from an uploaded package.
- First run asks how to address the user, editable in settings; an optional update check was added.
- Type annotations closed out, and release artifacts no longer carry internal notes.

## 0.1.1 — 2026-11-09

**npm-only republish**

- The npm package excludes internal documents and debug leftovers, aborting the pack when either is detected.
- Fixed empty 2 KB GitHub Release assets.
- Package metadata completed, so the PyPI page is no longer description-less.
- The dependency lockfile is generated for universal platforms, and CI gained a dependency audit.

## 0.1.0 — 2026-11-06

**First public release**, on PyPI and npm.

- Runtime: FastAPI service and a command-line entry point.
- Agents: three collaborating agents, with other capabilities demoted to instruction skills.
- Knowledge and memory embedded in the repository, with extraction, retrieval and spaced-repetition review.
- Governance: policy checks, audit records, shadow learning and a circuit breaker; skill side effects are denied by default.
- Config: typed reads and a configuration check command that turns misconfiguration into a clear message.
- Security: DNS rebinding, browser SSRF and untrusted channel instructions closed off; one-time confirmation for sensitive actions.
- Pipeline: visible plans, reasoning emitted as its own event and step-level resume checkpoints.
- Frontend: a Vue3 three-column renderer.

> Both packages are **placeholders**: the embedded runtimes are not shipped, so a fresh install only gets the importable skeleton. See [docs/RELEASE.md](docs/RELEASE.md).
