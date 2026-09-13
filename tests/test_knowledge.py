"""Tests for the projects and memories side of a claude.ai export.

This half is carried across whole rather than distilled, so the risks are
different: mangled filenames, and frontmatter colliding with frontmatter.
"""

import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_distill.knowledge import (  # noqa: E402
    MemoryFile, load_memories, load_project, render_memory, render_project,
    write_knowledge,
)


def _export(projects=None, memories=None):
    """Build an extracted-export directory tree in a temp dir."""
    root = tempfile.mkdtemp()
    if projects is not None:
        d = os.path.join(root, "projects-000", "projects")
        os.makedirs(d)
        for i, p in enumerate(projects):
            with io.open(os.path.join(d, f"p{i}.json"), "w", encoding="utf-8") as fh:
                json.dump(p, fh)
    if memories is not None:
        d = os.path.join(root, "memories-000", "memories")
        os.makedirs(d)
        with io.open(os.path.join(d, "m.json"), "w", encoding="utf-8") as fh:
            json.dump(memories, fh)
    return root


PROJECT = {
    "uuid": "p-1", "name": "Deep learning assignment", "description": "assessment 1",
    "prompt_template": "always show the loss curve",
    "created_at": "2026-09-03T10:00:00Z", "updated_at": "2026-09-03T11:00:00Z",
    "docs": [{"filename": "spec.md", "content": "the spec body"}],
}


class TestProjects(unittest.TestCase):
    def test_fields_and_docs_render(self):
        root = _export(projects=[PROJECT])
        path = os.path.join(root, "projects-000", "projects", "p0.json")
        out = render_project(load_project(path))
        self.assertIn('title: "Deep learning assignment"', out)
        self.assertIn("always show the loss curve", out)
        self.assertIn("### spec.md", out)
        self.assertIn("the spec body", out)

    def test_empty_doc_is_marked_not_dropped(self):
        p = dict(PROJECT, docs=[{"filename": "blank.md", "content": ""}])
        root = _export(projects=[p])
        out = render_project(load_project(
            os.path.join(root, "projects-000", "projects", "p0.json")))
        self.assertIn("### blank.md", out)
        self.assertIn("_empty_", out)


class TestMemories(unittest.TestCase):
    def test_memory_files_and_rolling_summary_both_yielded(self):
        root = _export(memories={
            "conversations_memory": "the rolling summary",
            "memory_files": [{"path": "/areas/thesis.md", "content": "body",
                              "updated_at": "2026-08-01T00:00:00Z"}],
        })
        items = list(load_memories(
            os.path.join(root, "memories-000", "memories", "m.json")))
        paths = [m.path for m in items]
        self.assertIn("conversations_memory", paths)
        self.assertIn("/areas/thesis.md", paths)

    def test_empty_memory_files_skipped(self):
        root = _export(memories={"memory_files": [{"path": "/x.md", "content": "  "}]})
        items = list(load_memories(
            os.path.join(root, "memories-000", "memories", "m.json")))
        self.assertEqual(items, [])

    def test_inner_frontmatter_is_merged_not_duplicated(self):
        """Obsidian honours only the first block, so there must be exactly one."""
        memory = MemoryFile(path="/preferences.md",
                            content="---\nname: preferences\ndesc: how to respond\n---\nthe body")
        out = render_memory(memory)
        self.assertEqual(out.count("---"), 2)
        self.assertIn("name: preferences", out)
        self.assertIn("the body", out)

    def test_inner_key_cannot_shadow_ours(self):
        memory = MemoryFile(path="/x.md", content="---\ntitle: hijacked\n---\nbody")
        out = render_memory(memory)
        self.assertIn('title: "x"', out)
        self.assertNotIn("title: hijacked", out)

    def test_content_without_frontmatter_is_untouched(self):
        out = render_memory(MemoryFile(path="/plain.md", content="just text"))
        self.assertIn("just text", out)
        self.assertEqual(out.count("---"), 2)


class TestFilenames(unittest.TestCase):
    def test_leading_slash_and_double_extension_removed(self):
        root = _export(memories={
            "memory_files": [{"path": "/areas/thesis.md", "content": "body"}]})
        out_dir = tempfile.mkdtemp()
        write_knowledge(root, out_dir)
        names = os.listdir(out_dir)
        self.assertIn("memory - areas - thesis.md", names)
        self.assertFalse(any(n.endswith(".md.md") for n in names))

    def test_counts_returned(self):
        root = _export(projects=[PROJECT],
                       memories={"memory_files": [{"path": "/a.md", "content": "x"}]})
        n_projects, n_memories = write_knowledge(root, tempfile.mkdtemp())
        self.assertEqual((n_projects, n_memories), (1, 1))

    def test_light_metadata_is_ignored(self):
        """login_history and users.json hold PII and must never reach the vault."""
        root = _export(projects=[PROJECT])
        skipped = os.path.join(root, "light_metadata-000", "projects")
        os.makedirs(skipped)
        with io.open(os.path.join(skipped, "x.json"), "w", encoding="utf-8") as fh:
            json.dump(dict(PROJECT, name="SHOULD NOT APPEAR"), fh)
        out_dir = tempfile.mkdtemp()
        write_knowledge(root, out_dir)
        self.assertFalse(any("SHOULD NOT APPEAR" in n for n in os.listdir(out_dir)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
