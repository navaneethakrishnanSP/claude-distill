#!/usr/bin/env python3
"""Distil Claude Code session transcripts into signal-only Markdown.

A transcript is mostly noise: tool output, file dumps, model reasoning. The
parts worth keeping are what the human asked for, where they corrected course,
and what actually changed on disk. This extracts those and throws away the rest.

Deterministic: no model calls, no network, stdlib only. The same transcript
always produces the same output, which is what makes it safe to re-run nightly.
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator

__all__ = ["Session", "distil_file", "iter_records"]

# A human turn that starts with one of these is usually the user pulling the
# agent off a wrong path, which makes it the densest signal in the transcript.
CORRECTION_MARKERS = re.compile(
    r"\b(no+|nope|don'?t|do not|stop|wrong|instead|actually|not (?:like )?that|"
    r"you don'?t understand|u dont understand|not what i|revert|undo|rather than)\b",
    re.I,
)

# Text the harness injects into the user role. None of it was typed by a human.
SYNTHETIC_PREFIXES = (
    "<system-reminder>",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<local-command-caveat>",
    "<user-prompt-submit-hook>",
    "Caveat: The messages below",
    "[Request interrupted",
)

# Harness wrappers that can precede real typed text in the same block. Strip
# them first, then decide whether anything human is left.
WRAPPER_TAGS = re.compile(
    r"<(system-reminder|command-name|command-message|command-args|"
    r"local-command-stdout|local-command-stderr|local-command-caveat|"
    r"user-prompt-submit-hook)>.*?</\1>",
    re.S | re.I,
)

INTERRUPT_MARKER = "[Request interrupted"

# Tools whose use means the working tree actually changed.
MUTATING_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def iter_records(path: str) -> Iterator[dict]:
    """Yield parsed JSON objects from a .jsonl transcript, skipping bad lines.

    Transcripts are appended to live, so a truncated final line is normal and
    must not abort the run.
    """
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _blocks(message: Any) -> list[dict]:
    """Normalise message content to a list of typed blocks."""
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _is_synthetic(text: str) -> bool:
    stripped = text.lstrip()
    return any(stripped.startswith(p) for p in SYNTHETIC_PREFIXES)


def _human_text(record: dict) -> str | None:
    """Return what the human actually typed in this record, if anything.

    A 'user' record is only a real prompt when it carries no tool_result: the
    harness reuses the user role to deliver tool output back to the model.
    """
    message = record.get("message")
    if not isinstance(message, dict) or message.get("role") != "user":
        return None
    blocks = _blocks(message)
    if any(b.get("type") == "tool_result" for b in blocks):
        return None
    parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
    text = "\n".join(p for p in parts if p).strip()
    if not text:
        return None
    # Strip wrappers before judging: a slash command arrives as tags around its
    # expansion, and whatever the human typed alongside it is what we want.
    # Checking for synthetic prefixes first would discard that text with them.
    text = WRAPPER_TAGS.sub("", text).strip()
    if not text or _is_synthetic(text) or text.startswith("<"):
        return None
    return text


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


@dataclass
class Session:
    """Everything worth keeping from one transcript."""

    session_id: str = ""
    path: str = ""
    cwd: str = ""
    branch: str = ""
    source: str = "claude-code"  # or "claude-web"
    name: str = ""  # explicit title, when the source provides one
    started: str = ""
    ended: str = ""
    prompts: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)
    interrupts: int = 0
    files_touched: Counter = field(default_factory=Counter)
    commands: list[str] = field(default_factory=list)
    tool_counts: Counter = field(default_factory=Counter)
    total_records: int = 0

    @property
    def date(self) -> str:
        return (self.started or "")[:10]

    @property
    def title(self) -> str:
        """The conversation's own name if it has one, else the first prompt."""
        if self.name:
            return _shorten(self.name, 70)
        return _shorten(self.prompts[0], 70) if self.prompts else "(no prompt)"

    @property
    def signal_ratio(self) -> float:
        """Share of records that survived distillation."""
        if not self.total_records:
            return 0.0
        kept = len(self.prompts) + len(self.commands) + len(self.files_touched)
        return kept / self.total_records


def distil_file(path: str, *, max_command_len: int = 120) -> Session:
    """Extract the signal from a single transcript."""
    session = Session(path=path)
    seen_commands: set[str] = set()

    for record in iter_records(path):
        session.total_records += 1

        session.session_id = session.session_id or record.get("sessionId", "") or ""
        session.cwd = session.cwd or record.get("cwd", "") or ""
        session.branch = session.branch or record.get("gitBranch", "") or ""
        timestamp = record.get("timestamp") or ""
        if timestamp:
            session.started = session.started or timestamp
            session.ended = timestamp

        message = record.get("message")
        if not isinstance(message, dict):
            continue

        text = _human_text(record)
        if text is not None:
            session.prompts.append(text)
            if CORRECTION_MARKERS.search(text[:200]):
                session.corrections.append(text)
            continue

        for block in _blocks(message):
            btype = block.get("type")
            if btype == "text" and INTERRUPT_MARKER in block.get("text", ""):
                session.interrupts += 1
            elif btype == "tool_use":
                name = block.get("name", "?")
                session.tool_counts[name] += 1
                payload = block.get("input") or {}
                if not isinstance(payload, dict):
                    continue
                if name in MUTATING_TOOLS:
                    target = payload.get("file_path") or payload.get("notebook_path")
                    if target:
                        session.files_touched[target] += 1
                elif name in ("Bash", "PowerShell"):
                    command = _shorten(str(payload.get("command", "")), max_command_len)
                    if command and command not in seen_commands:
                        seen_commands.add(command)
                        session.commands.append(command)

    return session


def _wikilink(path: str) -> str:
    return f"[[{os.path.basename(path)}]]"


def render_markdown(session: Session, *, max_prompts: int = 40) -> str:
    """Render one session as an Obsidian-friendly note."""
    out: list[str] = []
    out.append("---")
    out.append(f'title: "{session.title.replace(chr(34), chr(39))}"')
    out.append(f"date: {session.date}")
    out.append(f"session_id: {session.session_id}")
    out.append(f"source: {session.source}")
    out.append(f"cwd: {session.cwd}")
    if session.branch:
        out.append(f"branch: {session.branch}")
    out.append(f"prompts: {len(session.prompts)}")
    out.append(f"corrections: {len(session.corrections)}")
    out.append(f"interrupts: {session.interrupts}")
    out.append(f"files_touched: {len(session.files_touched)}")
    out.append(f"tags: [claude-session, {session.source}]")
    out.append("---")
    out.append("")
    out.append(f"# {session.title}")
    out.append("")
    out.append(
        f"`{session.started[:19]}` to `{session.ended[:19]}` - "
        f"{session.total_records} records distilled to "
        f"{len(session.prompts)} prompts, {len(session.commands)} distinct commands."
    )
    out.append("")

    if session.corrections:
        out.append("## Corrections")
        out.append("")
        out.append("Where the run went off course. Densest signal in the session.")
        out.append("")
        for item in session.corrections:
            out.append(f"- {_shorten(item, 300)}")
        out.append("")

    if session.interrupts:
        out.append(f"> Interrupted by the user {session.interrupts} time(s).")
        out.append("")

    if session.prompts:
        out.append("## Asked for")
        out.append("")
        for item in session.prompts[:max_prompts]:
            out.append(f"- {_shorten(item, 240)}")
        if len(session.prompts) > max_prompts:
            out.append(f"- _...{len(session.prompts) - max_prompts} more_")
        out.append("")

    if session.files_touched:
        out.append("## Files changed")
        out.append("")
        for path, count in session.files_touched.most_common():
            suffix = f" ({count}x)" if count > 1 else ""
            out.append(f"- `{path}`{suffix} {_wikilink(path)}")
        out.append("")

    if session.commands:
        out.append("## Commands run")
        out.append("")
        out.append("```console")
        out.extend(session.commands)
        out.append("```")
        out.append("")

    if session.tool_counts:
        summary = ", ".join(
            f"{name} {count}" for name, count in session.tool_counts.most_common(10)
        )
        out.append(f"## Tool use\n\n{summary}\n")

    return "\n".join(out)


def render_index(sessions: Iterable[Session]) -> str:
    ordered = sorted(sessions, key=lambda s: s.started or "", reverse=True)
    out = ["---", "title: Session index", "tags: [claude-session, index]", "---", ""]
    out.append("# Session index")
    out.append("")
    out.append("| Date | Session | Prompts | Corrections | Files |")
    out.append("| --- | --- | --- | --- | --- |")
    for s in ordered:
        name = f"{s.date}-{s.session_id[:8]}"
        out.append(
            f"| {s.date} | [[{name}]] {_shorten(s.title, 50)} | {len(s.prompts)} "
            f"| {len(s.corrections)} | {len(s.files_touched)} |"
        )
    out.append("")
    return "\n".join(out)


def default_projects_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude", "projects")


def find_transcripts(projects_dir: str, project: str | None) -> list[str]:
    pattern = os.path.join(projects_dir, project or "*", "*.jsonl")
    return sorted(glob.glob(pattern))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="claude-distill",
        description="Distil Claude Code transcripts into signal-only Markdown notes.",
    )
    parser.add_argument("--projects-dir", default=default_projects_dir(),
                        help="root of ~/.claude/projects")
    parser.add_argument("--project", default=None,
                        help="single project directory name (default: all)")
    parser.add_argument("--out", default="distilled",
                        help="output directory for Markdown notes")
    parser.add_argument("--format", choices=("md", "json"), default="md")
    parser.add_argument("--min-prompts", type=int, default=1,
                        help="skip sessions with fewer real prompts than this")
    parser.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                        help="only sessions on or after this date")
    parser.add_argument("--stats", action="store_true",
                        help="print a compression summary and exit")
    parser.add_argument("--web-export", default=None, metavar="conversations.json",
                        help="also read a claude.ai data export "
                             "(Settings > Privacy > Export data)")
    parser.add_argument("--web-only", action="store_true",
                        help="read only the web export, skip local transcripts")
    args = parser.parse_args(argv)

    if args.web_only and not args.web_export:
        print("--web-only needs --web-export", file=sys.stderr)
        return 1

    transcripts: list[str] = []
    if not args.web_only:
        transcripts = find_transcripts(args.projects_dir, args.project)
        if not transcripts and not args.web_export:
            print(f"no transcripts under {args.projects_dir}", file=sys.stderr)
            return 1

    candidates: list[Session] = []
    raw_bytes = 0
    for path in transcripts:
        raw_bytes += os.path.getsize(path)
        candidates.append(distil_file(path))

    if args.web_export:
        from .webexport import load_web_export
        try:
            raw_bytes += os.path.getsize(args.web_export)
            candidates.extend(load_web_export(args.web_export))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"could not read {args.web_export}: {exc}", file=sys.stderr)
            return 1

    sessions: list[Session] = []
    for session in candidates:
        if len(session.prompts) < args.min_prompts:
            continue
        if args.since and session.date and session.date < args.since:
            continue
        sessions.append(session)

    if not sessions:
        print("no sessions matched the filters", file=sys.stderr)
        return 1

    if args.stats:
        out_bytes = sum(len(render_markdown(s).encode("utf-8")) for s in sessions)
        ratio = raw_bytes / out_bytes if out_bytes else 0
        print(f"transcripts : {len(transcripts)}")
        web = sum(1 for x in sessions if x.source == "claude-web")
        if web:
            print(f"web chats   : {web}")
        print(f"sessions    : {len(sessions)}")
        print(f"raw         : {raw_bytes/1_048_576:.1f} MB")
        print(f"distilled   : {out_bytes/1024:.1f} KB")
        print(f"compression : {ratio:.0f}x")
        print(f"corrections : {sum(len(s.corrections) for s in sessions)}")
        print(f"interrupts  : {sum(s.interrupts for s in sessions)}")
        return 0

    os.makedirs(args.out, exist_ok=True)

    if args.format == "json":
        payload = [
            {
                "session_id": s.session_id, "date": s.date, "cwd": s.cwd,
                "source": s.source,
                "branch": s.branch, "title": s.title, "prompts": s.prompts,
                "corrections": s.corrections, "interrupts": s.interrupts,
                "files_touched": dict(s.files_touched), "commands": s.commands,
                "tool_counts": dict(s.tool_counts),
            }
            for s in sessions
        ]
        target = os.path.join(args.out, "sessions.json")
        with io.open(target, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        print(f"wrote {target}")
        return 0

    for session in sessions:
        name = f"{session.date}-{session.session_id[:8]}.md"
        target = os.path.join(args.out, name)
        with io.open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render_markdown(session))

    index = os.path.join(args.out, "index.md")
    with io.open(index, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_index(sessions))

    print(f"wrote {len(sessions)} notes + index to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
