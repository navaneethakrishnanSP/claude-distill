# claude-distill

Claude Code writes a `.jsonl` transcript for every session. They get big fast, and
almost all of it is tool output, file dumps and model reasoning. The useful parts are
what you asked for, where you corrected the agent, and what changed on disk.

This pulls those out and drops the rest.

Running it over 11 sessions here:

```
transcripts : 11
sessions    : 10
raw         : 72.1 MB
distilled   : 180.1 KB
compression : 410x
corrections : 31
interrupts  : 10
```

## Why not just use an existing tool

There are tools that read these transcripts already, like claude-code-log and cctrace.
They render a transcript so you can read it. They don't reduce it, so a 33 MB session
is still a 33 MB session.

Memory tools like claude-mem go the other way and summarise with an LLM. That works,
but it costs money, needs network, and gives you a slightly different answer every time
you run it.

This one uses heuristics and the standard library. No API calls, no network, no
dependencies. The same transcript always gives the same output, so you can run it on a
schedule without thinking about it.

## Install

Python 3.9 or newer. Nothing else.

```
git clone https://github.com/navaneethakrishnanSP/claude-distill
cd claude-distill
python -m claude_distill.distill --stats
```

## Usage

Check how much noise you are carrying:

```
python -m claude_distill.distill --stats
```

Write one Markdown file per session plus an index:

```
python -m claude_distill.distill --out ./distilled
```

Narrow it down:

```
python -m claude_distill.distill --project C--Users-me-code --since 2026-09-01
python -m claude_distill.distill --format json --out ./distilled
```

Options:

- `--projects-dir` where your `~/.claude/projects` lives
- `--project` a single project directory, default is all of them
- `--out` output directory
- `--format` `md` or `json`
- `--min-prompts` skip sessions shorter than this
- `--since` only sessions on or after a date
- `--stats` print the summary and stop

## Web chats

Claude Code sessions are on your disk, so those are read automatically. Your
claude.ai chats are not. They sit on Anthropic's servers and there is no API to
fetch your own history, so the only way to get them is the official export:

Settings, then Privacy, then Export data. You get an email with a ZIP. Inside is
`conversations.json`. The link expires after 24 hours.

Unzip everything and point the tool at the folder:

```
python -m claude_distill.distill --export-dir ./claude-export-2026-09-13 --out ./vault
```

That finds `conversations.json` on its own, and also picks up your projects and
memories. If you only have the conversations file, use `--web-export` instead,
and add `--web-only` to skip local transcripts.

### Projects and memories

An export has four archives. Conversations are the noisy part and get distilled.
Projects and memories are the opposite: already curated, so they are carried
across whole.

Projects become one note each, with the custom instructions and every attached
document. Memories become one note per memory file, plus the rolling
conversation memory. Memory files carry their own frontmatter, which is merged
into the note's rather than emitted twice, because Obsidian only reads the first
block.

This is the half worth keeping. A conversation you can roughly remember. A
prompt template you wrote six months ago you cannot.

The fourth archive, `light_metadata`, holds your login history and a
`users.json` with your email address and phone number. It is skipped on purpose.
None of it belongs in a notes vault.

Both sources end up in one directory with one index, tagged `claude-code` or
`claude-web` so you can tell them apart or filter on them in a vault.

The export is a ZIP containing `conversations.json`, plus separate archives for
projects, memories and account metadata. Only the conversations file is used.

Some messages carry no text at all: the text field is empty and the content block
list is empty too. Usually that is an old image-only upload with nothing retained.
Occasionally the text is in `attachments[].extracted_content` instead, which is a
file you pasted in. Those are excerpted to 300 characters rather than inlined,
because a pasted CSV can run to tens of thousands of characters and that is the
bulk this tool exists to remove.

Be clear about what this does and doesn't automate. The Claude Code half is fully
automatic, and you can run it from a hook or a scheduled task. The web half is not,
and can't be: the export is a manual step and nothing can trigger it for you. In
practice your Claude Code history stays current and your web history is a snapshot
you refresh when you remember to.

## What it keeps

Your prompts. Corrections, meaning turns where you told the agent it was going the
wrong way. Interruptions. Files that were written or edited, with a count each.
Distinct shell commands, deduplicated. A count of which tools the session used.

Corrections are the part worth reading. They tend to show where your setup keeps
failing you, and a correction you have made three times is usually something that
belongs in a config file instead.

## What it drops

Tool results, file dumps, reasoning blocks, and anything the harness injected into the
user role that you didn't type.

That last one matters more than it sounds. A record with `role: user` is not
necessarily something a human wrote. Claude Code puts tool results back into the
conversation using the user role, so a parser that treats every user record as a prompt
produces nonsense. This checks for `tool_result` blocks first, and strips wrapper tags
like `<system-reminder>` and `<command-message>` before deciding whether anything human
is left. Order matters there, because if you filter before stripping you throw away
real text that happened to sit next to a slash command.

## Output

Markdown with YAML frontmatter and wikilinks, so it drops straight into an Obsidian
vault if you use one.

```markdown
---
title: "fix the sweep harness"
date: 2026-09-02
session_id: 57dfac9b-...
prompts: 17
corrections: 1
interrupts: 0
tags: [claude-session]
---

## Corrections

- no, don't change the seed, that invalidates the comparison

## Files changed

- `sweep.py` (4x) [[sweep.py]]
```

Point `--out` at a folder inside a vault and the notes link themselves together.

## Limitations

Corrections are found by looking for words like "no", "don't", "actually" and
"instead". A politely worded correction gets missed, and a prompt that happens to
contain "actually" gets flagged when it shouldn't. This is a deliberate trade: the
rules are simple enough to read and adjust, and they cost nothing to run.

It doesn't try to pull decisions or reasoning out of the assistant's replies. Doing
that properly needs a model, and the point of this tool is that it doesn't use one.

`--since` compares against the first timestamp in the file.

Written against the transcript format as it stood in September 2026. If Anthropic
changes the schema, the tests in `tests/` will tell you what broke.

## Tests

```
python -m unittest discover -s tests
```

## License

MIT
