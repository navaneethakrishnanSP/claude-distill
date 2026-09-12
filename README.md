# claude-distill

Turn Claude Code session transcripts into signal-only Markdown.

A `.jsonl` transcript is mostly noise — tool output, file dumps, model reasoning.
The parts worth keeping are **what you asked for**, **where you corrected course**,
and **what actually changed on disk**. This extracts those and discards the rest.

On a real 72 MB corpus of 11 sessions:

```
transcripts : 11
sessions    : 10
raw         : 72.0 MB
distilled   : 179.5 KB
compression : 411x
corrections : 31
interrupts  : 10
```

## Why this exists

Tools that read Claude Code transcripts already exist — [`claude-code-log`](https://github.com/daaain/claude-code-log),
[`cctrace`](https://github.com/jimmc414/cctrace) — but they *format* transcripts, they don't
reduce them. Memory tools like `claude-mem` compress with an LLM, which costs money,
needs network, and gives a different answer every run.

`claude-distill` is deterministic: **stdlib only, no model calls, no network.** The same
transcript always produces the same output, which is what makes it safe to run nightly
from a cron job or a `SessionEnd` hook.

## Install

No dependencies. Python 3.9+.

```bash
git clone https://github.com/YOUR_USER/claude-distill
cd claude-distill
python -m claude_distill.distill --stats
```

## Use

```bash
# See how much noise you're carrying
python -m claude_distill.distill --stats

# Write one Markdown note per session, plus an index
python -m claude_distill.distill --out ./distilled

# Only one project, only recent sessions
python -m claude_distill.distill --project C--Users-me-code --since 2026-09-01

# Machine-readable instead
python -m claude_distill.distill --format json --out ./distilled
```

### Options

| Flag | Meaning |
| --- | --- |
| `--projects-dir` | Root of `~/.claude/projects` |
| `--project` | One project directory name (default: all) |
| `--out` | Output directory |
| `--format` | `md` (default) or `json` |
| `--min-prompts` | Skip sessions with fewer real prompts |
| `--since` | Only sessions on or after `YYYY-MM-DD` |
| `--stats` | Print a compression summary and exit |

## What it keeps

| Kept | Why |
| --- | --- |
| Human prompts | What you actually asked for |
| **Corrections** | Turns where you pulled the agent off a wrong path — the densest signal in any transcript |
| Interrupts | `[Request interrupted by user]` marks a run going wrong |
| Files changed | `Write` / `Edit` / `MultiEdit` / `NotebookEdit` targets, with counts |
| Distinct commands | Deduplicated `Bash` / `PowerShell` commands |
| Tool histogram | Which tools the session leaned on |

## What it throws away

Tool results, file dumps, model reasoning blocks, and harness-injected text that
was never typed by a human (`<system-reminder>`, slash-command expansions,
`<local-command-stdout>`, and friends).

That last category matters more than it sounds. A `user` record is **not**
necessarily a human turn — the harness reuses the user role to deliver tool
output back to the model. Naive parsers treat those as prompts and produce
garbage. This checks for `tool_result` blocks and strips wrapper tags first.

## Output

Obsidian-ready Markdown with YAML frontmatter and `[[wikilinks]]`:

```markdown
---
title: "fix the sweep harness"
date: 2026-09-02
session_id: 57dfac9b-...
cwd: C:\Users\me\projects
prompts: 17
corrections: 1
interrupts: 0
tags: [claude-session]
---

## Corrections

- no, don't change the seed - that invalidates the comparison

## Files changed

- `sweep.py` (4x) [[sweep.py]]
```

Point `--out` at a folder inside an Obsidian vault and the notes graph themselves.

## Limitations

- **Heuristic, not semantic.** Corrections are matched by lexical markers, so a
  politely-phrased correction is missed and a prompt containing "actually" is a
  false positive. Deliberate: heuristics are auditable and free.
- Does not extract *decisions* or *rationale* from assistant prose. That genuinely
  needs a model; this stays deterministic instead.
- `--since` filters on the first timestamp in the file.
- Tested against the transcript schema as of September 2026.

## License

MIT
