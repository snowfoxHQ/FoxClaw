# Content Gate and Lexicons

This layer does one thing: it makes a deterministic decision on **both the user request and the model output**, and cuts the turn off with a fixed line when something matches.

It does not call a model, does not do semantic understanding, and does not replace platform review or human moderation. The prompt layer keeps the model from crossing the line on its own; this layer holds the floor when either the model or the user gets around the prompt.

## Three sources of judgement

| Source | Where | Configurable | Covers |
|---|---|---|---|
| Built-in rules | `_BUILTIN_RULES` in `foxclaw/core/content_guard.py` | No (only `off` disables them) | Six **request-shaped** categories: drug/explosive manufacturing, fraud and money laundering, intrusion into someone else's systems, forged documents, suicide methods, sexual content involving minors |
| External lexicon | Files listed in `FOXCLAW_CONTENT_LEXICON` | Yes | Whatever you decide — political, sexual, violent categories all live here |
| Prompt | `KERNEL_PROMPT` §8 (Content boundaries) | Code change | Soft constraint; owns the tone and the refusal pattern |

The built-in rules are deliberately narrow: a **verb plus a target** has to appear together. "How do I make explosives" matches; "I bought a book on the history of explosives" does not. The goal is something that works out of the box without interrupting ordinary conversation, not full coverage of every violative phrasing.

**Large category lists are not built in.** Review requirements change, the blast radius of false positives is a deployment decision, and tens of thousands of terms do not belong in source. Whether to mount them, and which categories, is your call.

## Default behaviour

It is on out of the box, with no configuration:

- mode defaults to `enforce`
- only the built-in rules run when no lexicon is mounted
- a request that matches returns the fixed line immediately — **the request never reaches the model**
- an output that matches is replaced in full; no fragment of the original survives
- a streamed output that matches stops emitting at the match

The refusal follows the input language and exists in exactly two forms: `Let's talk about something else.` / `我们换个话题聊聊吧。`

No explanation, no elaboration, no counter-question, no alternative offered. A refusal must not become a channel for leaking information or continuing the thread.

## Three modes

```bash
FOXCLAW_CONTENT_GUARD_MODE=enforce   # default: actually block
FOXCLAW_CONTENT_GUARD_MODE=audit     # record only, for measuring false positives
FOXCLAW_CONTENT_GUARD_MODE=off       # disabled
```

An unrecognised value falls back to `enforce` — it over-blocks rather than failing silently.

`audit` hits are written to the **process stdout**:

```
[Foxclaw] ⚠ 内容命中(未拦截): category=politics word='...'
```

Only `audit` prints. `enforce` hits are silent. If you need an audit trail, wire `Decision.to_dict()` at the call site, or run `audit` for a while to collect a baseline first.

## Where to put the configuration

Two paths, different behaviour:

- **`.env` (recommended)**: the API service loads `.env` before importing the package, so values placed there take effect. Empty values count as "not configured" and never override a real environment variable.
- **Process environment**: when you use `Foxclaw()` from the SDK, `.env` is not loaded for you — export the variables before the process starts.

The `.env` load can be skipped with `FOXCLAW_SKIP_DOTENV=1`.

## Lexicon format

One term per line, UTF-8, `#` for comments:

```
# term<TAB>category; the category is optional
some-term	politics
another-term	sexual
term-without-category
```

The details below come from the loader, not from convention:

| Item | Behaviour |
|---|---|
| Separator | `term<TAB>category`; with no TAB the whole line is the term and the category becomes `platform` |
| Comments | lines starting with `#` are skipped |
| Whitespace and punctuation | every term goes through `compact()`: whitespace and CJK/ASCII punctuation stripped, NFKC-normalised, lowercased |
| Length | anything shorter than 2 characters **after** `compact()` is dropped (single characters cause too much collateral damage and rarely catch real violations) |
| Same term, several categories | allowed; scan results are deduplicated by `(category, term)` and the first hit wins |
| Bad lines, unreadable files | skipped silently — a bad lexicon must not prevent the service from starting |
| When it takes effect | the index is built **at process start**; editing a lexicon requires a restart, there is no hot reload |

Prefer **absolute paths**. Relative paths resolve against the process working directory, which under systemd is often not the project root.

After a restart, confirm how many terms actually loaded:

```bash
python -c "from foxclaw.core.content_guard import ContentGuard as C; print(C().lexicon_size)"
```

`0` means nothing was read.

## Converting a third-party list

Existing lists — the ~60k-term main dictionary of `houbb/sensitive-word`, or the versions already split into political / sexual / violent files — come in incompatible shapes: single file of bare terms or one file per category, GBK encoding, and the same term written three ways (`敏感词`, `敏 感 词`, `ＳＥＮＳＩＴＩＶＥ`).

`tools/build_content_lexicon.py` does the conversion. **The script lives in the repository and is not shipped in the wheel** — if you installed from PyPI, take it from the GitHub repository:

```bash
# single-file dictionary, everything under the `platform` category
python tools/build_content_lexicon.py sensitive_word_dict.txt -o content_lexicon.txt

# per-category files: a CJK file name becomes the category (政治类.txt → 政治类)
python tools/build_content_lexicon.py dict/*.txt -o content_lexicon.txt

# keep only what you want
python tools/build_content_lexicon.py dict/*.txt --exclude 政治类,色情类 --exclude "暴力*" -o content_lexicon.txt

# single file, explicit category name
python tools/build_content_lexicon.py ads.txt --category ads -o content_lexicon.txt
```

It does four things:

1. **Detects the encoding**: `utf-8-sig` first, then `gb18030`. Many lists downloaded inside China are GBK, while the loader reads UTF-8 and ignores undecodable bytes — skip this step and terms go missing silently after mounting.
2. **Normalises during conversion**, using the loader's own `compact()`, so duplicate spellings collapse now instead of bloating the automaton later.
3. **Filters by category**: `--include` / `--exclude` accept wildcards, comma-separated and repeatable.
4. **Verifies by reading back**: loads the output through a real `ContentGuard` and samples terms to confirm they still match; exits with code 1 if that fails.

Common options:

| Option | Default | Meaning |
|---|---|---|
| `-o, --output` | `content_lexicon.txt` | output path |
| `--category` | `platform` | category used when a file name has no CJK characters |
| `--include` / `--exclude` | none | category filters, wildcards supported |
| `--encoding` | `auto` | force a specific codec |
| `--min-len` | `2` | drop shorter terms (matches the loader) |
| `--max-len` | `0` | drop longer terms, `0` means no limit |
| `--sample` | `5` | terms sampled during verification |
| `--no-verify` | off | skip the read-back check |

At 60k terms, conversion plus load and verification takes about half a second.

Generated lexicons are ignored by git (`/content_lexicon*.txt`). Do not commit them.

## Run `audit` before you enforce

**This is the step most likely to be skipped, and the most expensive to skip.**

A 60k-term list inevitably contains a lot of two-character terms. Mount it under `enforce` and ordinary order discussions, manuscript reviews, or medical questions will get cut off — the whole output is replaced, the user gets no explanation, only a sudden "let's talk about something else".

Suggested sequence:

1. mount the lexicon, set `FOXCLAW_CONTENT_GUARD_MODE=audit`
2. use the product normally for a few days, collect the hit lines from stdout
3. review `category` and `word` for each: genuine violation or collateral damage
4. drop the noisy categories with `--exclude`, or regenerate with longer terms
5. switch to `enforce` once the rate is acceptable

There is no allowlist mechanism. The only levers against false positives are: mount fewer categories, use longer terms, raise `--min-len`.

## Fidelity, stated plainly

This is a **speed bump**, not a wall. It explicitly does not handle:

- pinyin, homophones, split characters, transliteration (endless, and worse for false positives)
- images, audio, attachment bodies
- multi-turn grooming (splitting a sentence across three turns)
- books, statutes, and medical texts imported into the knowledge base

That last point is intentional: **the filter only sits on "user request" and "model output"**, and never touches the user's own material. A history book that describes an explosive formula should not fail to import.

Streaming has an inherent limit: at the moment of the match it can only guarantee that nothing further is emitted — **a prefix already streamed out cannot be recalled**. The non-streaming path replaces the whole output and has no such gap.

## Troubleshooting

| Symptom | Check |
|---|---|
| Nothing is blocked at all | is `FOXCLAW_CONTENT_GUARD_MODE` set to `off`; did the variable actually reach the process |
| Built-in rules fire, lexicon does not | is `lexicon_size` 0; relative vs absolute path; file encoding |
| Some mounted terms never match | was the term under 2 characters after `compact()`; did a space become part of the term |
| Edited the lexicon, nothing changed | the process was not restarted; the index is built at start |
| Normal conversation gets cut | too many two-character terms; switch to `audit` and drop that category |

## Relationship to the prompt layer

Different jobs, both required:

- the prompt layer (`KERNEL_PROMPT` §8 plus `no_harmful_content` in `behavior.yaml`) owns the semantic boundary: what counts as violative, how a refusal should sound, how to tell the user's material from the user's instructions.
- the code layer (this document) owns the deterministic floor: a match is a match, no model judgement involved.

With only the prompt, a successful jailbreak leaves nothing behind it. With only the code, every edge case lives or dies by the lexicon, and answers turn mechanical. Together, the model knows where the line is and there is a backstop when it crosses anyway.

## Scope of responsibility

This repository ships no political, sexual, or violent term list. Whether to mount one, which categories, and which mode are deployment decisions — no policy is assumed here.

The layer is string matching after normalisation: it does not guarantee coverage and is not a content moderation or compliance service. Deployers who expose the service publicly are responsible for their own content compliance. The software is provided "as is", see [LICENSE](../LICENSE).
