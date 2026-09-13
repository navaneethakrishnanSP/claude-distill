"""Read the projects and memories from a claude.ai data export.

The conversations half of an export is mostly noise and needs distilling. This
half is the opposite: project instructions, project documents and memory files
are already curated, so they are carried across whole rather than reduced.

They are also the part you cannot reconstruct. A conversation you can roughly
remember; a 13KB prompt template you wrote six months ago you cannot.

Layout inside an extracted export:

    projects-000/projects/<uuid>.json    one per project
    memories-000/memories/<uuid>.json    one per account

Note: light_metadata contains login history and a users.json holding your email
address and phone number. It is deliberately ignored - none of it belongs in a
notes vault.
"""

from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass, field
from typing import Iterator

__all__ = ["Project", "MemoryFile", "find_projects", "load_project",
           "load_memories", "render_project", "render_memory"]

SKIP_DIRS = {"light_metadata-000", "conversations-000", "extracted"}


def _safe_name(text: str, fallback: str = "untitled") -> str:
    """Turn a title into something that can be a filename on Windows."""
    cleaned = "".join(c if c.isalnum() or c in " -_." else "-" for c in text).strip()
    cleaned = " ".join(cleaned.split())
    return (cleaned or fallback)[:60]


@dataclass
class Project:
    uuid: str = ""
    name: str = ""
    description: str = ""
    prompt_template: str = ""
    created_at: str = ""
    updated_at: str = ""
    docs: list[dict] = field(default_factory=list)

    @property
    def date(self) -> str:
        return (self.created_at or "")[:10]


@dataclass
class MemoryFile:
    path: str = ""
    content: str = ""
    updated_at: str = ""

    @property
    def date(self) -> str:
        return (self.updated_at or "")[:10]


def find_projects(export_dir: str) -> list[str]:
    """Locate every project JSON beneath an extracted export."""
    found = []
    for root, dirs, files in os.walk(export_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        if os.path.basename(root) != "projects":
            continue
        found.extend(os.path.join(root, f) for f in files if f.endswith(".json"))
    return sorted(found)


def find_memories(export_dir: str) -> list[str]:
    found = []
    for root, dirs, files in os.walk(export_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        if os.path.basename(root) != "memories":
            continue
        found.extend(os.path.join(root, f) for f in files if f.endswith(".json"))
    return sorted(found)


def load_project(path: str) -> Project:
    with io.open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return Project(
        uuid=data.get("uuid", "") or "",
        name=data.get("name", "") or "Untitled project",
        description=data.get("description", "") or "",
        prompt_template=data.get("prompt_template", "") or "",
        created_at=data.get("created_at", "") or "",
        updated_at=data.get("updated_at", "") or "",
        docs=[d for d in (data.get("docs") or []) if isinstance(d, dict)],
    )


def load_memories(path: str) -> Iterator[MemoryFile]:
    """Yield each memory file, plus the rolling conversation memory as one note."""
    with io.open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    summary = (data.get("conversations_memory") or "").strip()
    if summary:
        yield MemoryFile(path="conversations_memory", content=summary)

    for entry in data.get("memory_files") or []:
        if not isinstance(entry, dict):
            continue
        content = (entry.get("content") or "").strip()
        if not content:
            continue
        yield MemoryFile(
            path=entry.get("path", "") or "untitled",
            content=content,
            updated_at=entry.get("updated_at", "") or "",
        )

    project_memories = data.get("project_memories")
    if isinstance(project_memories, dict):
        for key, value in project_memories.items():
            text = value if isinstance(value, str) else json.dumps(value, indent=2)
            if text and text.strip():
                yield MemoryFile(path=f"project_memory/{key}", content=text.strip())


def render_project(project: Project) -> str:
    out = ["---",
           f'title: "{project.name.replace(chr(34), chr(39))}"',
           f"uuid: {project.uuid}",
           f"created: {project.date}",
           f"docs: {len(project.docs)}",
           "source: claude-web",
           "tags: [claude-project]",
           "---",
           "",
           f"# {project.name}",
           ""]
    if project.description:
        out += [project.description, ""]

    if project.prompt_template:
        out += ["## Instructions", "",
                "The custom instructions attached to this project.", "",
                "```", project.prompt_template.strip(), "```", ""]

    if project.docs:
        out += ["## Documents", ""]
        for doc in project.docs:
            filename = doc.get("filename") or "untitled"
            content = (doc.get("content") or "").strip()
            out += [f"### {filename}", ""]
            if content:
                out += [content, ""]
            else:
                out += ["_empty_", ""]
    return "\n".join(out)


def _split_frontmatter(text: str) -> tuple[list[str], str]:
    """Separate a leading YAML frontmatter block from the body.

    Memory files carry their own frontmatter. Emitting it inside a note that
    already has frontmatter gives Obsidian two blocks, and it only honours the
    first - so the inner one is lifted into ours instead.
    """
    if not text.startswith("---"):
        return [], text
    lines = text.splitlines()
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:]).strip()
    return [], text


def render_memory(memory: MemoryFile) -> str:
    stem = memory.path.strip("/")
    if stem.lower().endswith(".md"):
        stem = stem[:-3]
    title = stem or memory.path

    inner, body = _split_frontmatter(memory.content)
    reserved = {"title", "path", "updated", "source", "tags"}

    out = ["---", f'title: "{title.replace(chr(34), chr(39))}"']
    for line in inner:
        # Keep the memory's own keys, but never let one shadow ours.
        if line.split(":", 1)[0].strip() not in reserved:
            out.append(line)
    out += [f"path: {memory.path}",
            f"updated: {memory.date}",
            "source: claude-web",
            "tags: [claude-memory]",
            "---",
            "",
            f"# {title}",
            "",
            body,
            ""]
    return "\n".join(out)


def write_knowledge(export_dir: str, out_dir: str) -> tuple[int, int]:
    """Write project and memory notes into the vault. Returns (projects, memories)."""
    os.makedirs(out_dir, exist_ok=True)

    projects = 0
    for path in find_projects(export_dir):
        project = load_project(path)
        target = os.path.join(out_dir, f"project - {_safe_name(project.name)}.md")
        with io.open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render_project(project))
        projects += 1

    memories = 0
    for path in find_memories(export_dir):
        for memory in load_memories(path):
            # Memory paths look like "/areas/thesis.md": drop the leading slash
            # and the existing extension so the note isn't named "- - x.md.md".
            stem = memory.path.strip("/")
            if stem.lower().endswith(".md"):
                stem = stem[:-3]
            name = _safe_name(stem.replace("/", " - "), "memory")
            target = os.path.join(out_dir, f"memory - {name}.md")
            with io.open(target, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(render_memory(memory))
            memories += 1

    return projects, memories
