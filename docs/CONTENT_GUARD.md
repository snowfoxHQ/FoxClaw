# Content guard and custom lexicons

The content guard performs deterministic checks on both **user requests** and **model output**, ending the response with a fixed message on a hit. It does not call a model or attempt semantic understanding, and it does not replace platform review or human moderation — prompts keep the model itself in bounds, while this layer holds the line if it steps over.

Built-in rules are on by default and cover only explicit solicitation of clearly illegal or harmful content. Matching uses an action-plus-object combination, so ordinary mentions (for example, a term in a book or paper) do not trigger it.

No political, sexual or violent word lists are bundled. Whether to mount one, and which categories to include, is the deployer's decision.

## Modes

```bash
FOXCLAW_CONTENT_GUARD_MODE=enforce   # default: block
FOXCLAW_CONTENT_GUARD_MODE=audit     # log only, for tuning false positives
FOXCLAW_CONTENT_GUARD_MODE=off       # disabled
```

An invalid value falls back to `enforce` rather than silently disabling the guard. In `audit` mode, hits are written to process stdout with their category and matched term.

## Where to configure

- **`.env` (recommended)**: read at startup; restart after editing.
- **Environment variables**: when using the SDK directly, set them before the process starts.

Loading `.env` can be disabled with `FOXCLAW_SKIP_DOTENV=1`.

## Lexicon format

One term per line, UTF-8, with `#` starting a comment:

```
# term<TAB>category; category is optional
some term	politics
another term	sexual
term without a category
```

Loading rules:

| Item | Behaviour |
|---|---|
| Separator | `term<TAB>category`; without a TAB the whole line is the term and the category is `platform` |
| Comments | Lines starting with `#` are skipped |
| Normalization | Whitespace and punctuation removed, full/half width unified, lowercased |
| Length | Terms shorter than 2 characters after normalization are dropped |
| Duplicate terms with different categories | Allowed; the first category is used |
| Bad lines / unreadable files | Skipped silently; the service still starts |
| Effective timing | Index built at process start; restart after editing |

Absolute paths are safest; relative paths are resolved from the process working directory.

## Converting a third-party list

`tools/build_content_lexicon.py` converts existing word lists into the required format. The script is in the repository and is not shipped in the wheel.

```bash
python tools/build_content_lexicon.py sensitive_word_dict.txt -o content_lexicon.txt
python tools/build_content_lexicon.py dict/*.txt -o content_lexicon.txt
python tools/build_content_lexicon.py dict/*.txt --exclude politics,sexual -o content_lexicon.txt
python tools/build_content_lexicon.py ads.txt --category ads -o content_lexicon.txt
```

It detects encoding (UTF-8 / GB18030), filters by category, merges duplicate spellings, and reads the output back to verify it loads.

| Option | Default | Description |
|---|---|---|
| `-o, --output` | `content_lexicon.txt` | Output path |
| `--category` | `platform` | Category used when the file name has no category |
| `--include` / `--exclude` | none | Category filters, with `glob*` support |
| `--encoding` | `auto` | Force an encoding |
| `--min-len` | `2` | Drop shorter terms |
| `--max-len` | `0` | Drop longer terms; 0 means no limit |
| `--sample` | `5` | Verification sample size |
| `--no-verify` | off | Skip load-back verification |

Converted files are excluded in `.gitignore` and should not be committed.

## Run in audit mode first

Large lists often contain many two-character terms. Enabling blocking immediately can cut off ordinary order discussions, manuscript reviews or medical Q&A. Recommended flow:

1. Mount the list and set `audit`.
2. Use the product normally for a few days and collect hits from stdout.
3. Review which hits were legitimate and which were false positives.
4. Exclude categories with many false positives, or regenerate with longer terms.
5. Switch to `enforce` once the result is acceptable.

There is no allowlist; the ways to reduce false positives are to mount fewer categories, use longer terms, or raise `--min-len`.

## Troubleshooting

| Symptom | Check |
|---|---|
| Nothing is blocked | Whether the mode is `off`; whether the environment variable reached the process |
| Built-in rules work, custom list does not | Whether the loaded count is 0; path relative vs absolute; file encoding |
| Some terms are not blocked | Whether normalization dropped them as shorter than 2 characters; stray spaces |
| Editing the list has no effect | Whether the process was restarted |
| Normal conversation is cut off | Too many short terms; switch to `audit` or exclude the category |

## Responsibility boundary

This layer is normalized string matching. It does not guarantee coverage and is not a content-moderation or compliance service. Compliance responsibility for public-facing deployments rests with the deployer. The software is provided "as is"; see [LICENSE](../LICENSE).
