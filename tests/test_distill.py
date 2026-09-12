"""Tests for the parts that are easy to get wrong.

The core hazard: a 'user' record is not necessarily a human turn. The harness
reuses the user role to deliver tool results and injected reminders. Treating
those as prompts is the bug that makes naive transcript parsers useless.
"""

import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_distill.distill import distil_file, render_markdown  # noqa: E402


def _write(records):
    fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                     encoding="utf-8", newline="")
    for record in records:
        fh.write(json.dumps(record) + "\n")
    fh.close()
    return fh.name


def user(content, **extra):
    base = {"type": "user", "timestamp": "2026-09-01T10:00:00Z",
            "sessionId": "abc12345-0000", "cwd": r"C:\p",
            "message": {"role": "user", "content": content}}
    base.update(extra)
    return base


def assistant(content):
    return {"type": "assistant", "timestamp": "2026-09-01T10:00:01Z",
            "sessionId": "abc12345-0000",
            "message": {"role": "assistant", "content": content}}


class TestHumanTurnDetection(unittest.TestCase):
    def test_plain_string_is_a_prompt(self):
        s = distil_file(_write([user("fix the sweep harness")]))
        self.assertEqual(s.prompts, ["fix the sweep harness"])

    def test_tool_result_is_not_a_prompt(self):
        """The single most important case: tool output wears the user role."""
        records = [user([{"type": "tool_result", "content": "total 40\ndrwxr..."}])]
        s = distil_file(_write(records))
        self.assertEqual(s.prompts, [])

    def test_system_reminder_is_not_a_prompt(self):
        records = [user([{"type": "text",
                          "text": "<system-reminder>memory is old</system-reminder>"}])]
        self.assertEqual(distil_file(_write(records)).prompts, [])

    def test_slash_command_wrapper_is_stripped(self):
        records = [user([{"type": "text",
                          "text": "<command-message>doctor</command-message>"}])]
        self.assertEqual(distil_file(_write(records)).prompts, [])

    def test_text_alongside_wrapper_survives(self):
        records = [user([{"type": "text",
                          "text": "<command-name>/plan</command-name>now do it"}])]
        self.assertEqual(distil_file(_write(records)).prompts, ["now do it"])


class TestCorrections(unittest.TestCase):
    def test_correction_is_flagged(self):
        s = distil_file(_write([user("no, don't change the seed")]))
        self.assertEqual(len(s.corrections), 1)

    def test_ordinary_prompt_is_not_a_correction(self):
        s = distil_file(_write([user("add a docstring to sweep.py")]))
        self.assertEqual(s.corrections, [])

    def test_interrupt_is_counted(self):
        records = [assistant([{"type": "text",
                               "text": "[Request interrupted by user]"}])]
        self.assertEqual(distil_file(_write(records)).interrupts, 1)


class TestActions(unittest.TestCase):
    def test_edits_are_attributed_to_files(self):
        records = [assistant([
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}},
        ])]
        self.assertEqual(distil_file(_write(records)).files_touched["a.py"], 2)

    def test_commands_are_deduplicated(self):
        records = [assistant([
            {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ruff check"}},
        ])]
        self.assertEqual(distil_file(_write(records)).commands,
                         ["pytest -q", "ruff check"])


class TestRobustness(unittest.TestCase):
    def test_truncated_final_line_does_not_abort(self):
        """Transcripts are appended to live; a half-written last line is normal."""
        path = _write([user("first")])
        with io.open(path, "a", encoding="utf-8", newline="") as fh:
            fh.write('{"type": "user", "mess')
        self.assertEqual(distil_file(path).prompts, ["first"])

    def test_render_is_valid_without_optional_sections(self):
        out = render_markdown(distil_file(_write([user("hello")])))
        self.assertTrue(out.startswith("---"))
        self.assertIn("## Asked for", out)
        self.assertNotIn("## Corrections", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
