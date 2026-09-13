"""Tests for the claude.ai data export reader.

Built against the export schema as documented in September 2026. Unlike the
.jsonl side there is no tool_result ambiguity here: `sender` is authoritative.
"""

import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_distill.webexport import load_web_export, parse_conversation  # noqa: E402


def conversation(messages, name="A chat", uuid="conv-1234-5678"):
    return {
        "uuid": uuid,
        "name": name,
        "created_at": "2026-09-01T10:00:00Z",
        "updated_at": "2026-09-01T11:00:00Z",
        "chat_messages": messages,
    }


def msg(sender, text=None, content=None):
    out = {"uuid": "m1", "sender": sender, "created_at": "2026-09-01T10:00:00Z"}
    if text is not None:
        out["text"] = text
    if content is not None:
        out["content"] = content
    return out


def write_export(payload):
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8", newline="")
    json.dump(payload, fh)
    fh.close()
    return fh.name


class TestParsing(unittest.TestCase):
    def test_human_messages_become_prompts(self):
        s = parse_conversation(conversation([
            msg("human", "how do I seed this"),
            msg("assistant", "you can use random.seed"),
        ]))
        self.assertEqual(s.prompts, ["how do I seed this"])

    def test_assistant_messages_are_ignored(self):
        s = parse_conversation(conversation([msg("assistant", "here you go")]))
        self.assertEqual(s.prompts, [])

    def test_content_blocks_used_when_text_is_empty(self):
        s = parse_conversation(conversation([
            msg("human", text="", content=[{"type": "text", "text": "from blocks"}]),
        ]))
        self.assertEqual(s.prompts, ["from blocks"])

    def test_non_text_blocks_skipped(self):
        s = parse_conversation(conversation([
            msg("human", text="", content=[{"type": "image", "source": "..."}]),
        ]))
        self.assertEqual(s.prompts, [])

    def test_corrections_detected(self):
        s = parse_conversation(conversation([msg("human", "no, that's wrong")]))
        self.assertEqual(len(s.corrections), 1)

    def test_metadata_carried_over(self):
        s = parse_conversation(conversation([msg("human", "hi")], name="Seeding runs"))
        self.assertEqual(s.source, "claude-web")
        self.assertEqual(s.title, "Seeding runs")
        self.assertEqual(s.date, "2026-09-01")
        self.assertEqual(s.session_id, "conv-1234-5678")

    def test_web_sessions_have_no_tool_activity(self):
        s = parse_conversation(conversation([msg("human", "hi")]))
        self.assertEqual(s.commands, [])
        self.assertEqual(dict(s.files_touched), {})
        self.assertEqual(s.interrupts, 0)


class TestLoading(unittest.TestCase):
    def test_loads_a_list(self):
        path = write_export([conversation([msg("human", "one")], uuid="a"),
                             conversation([msg("human", "two")], uuid="b")])
        self.assertEqual(len(load_web_export(path)), 2)

    def test_accepts_a_single_object(self):
        path = write_export(conversation([msg("human", "solo")]))
        self.assertEqual(len(load_web_export(path)), 1)

    def test_one_bad_conversation_does_not_lose_the_rest(self):
        path = write_export([conversation([msg("human", "good")]), "not-a-dict",
                             conversation([msg("human", "also good")], uuid="c")])
        self.assertEqual(len(load_web_export(path)), 2)

    def test_missing_chat_messages_is_survivable(self):
        path = write_export([{"uuid": "x", "name": "empty",
                              "created_at": "2026-09-01T10:00:00Z",
                              "updated_at": "2026-09-01T10:00:00Z"}])
        sessions = load_web_export(path)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].prompts, [])

    def test_wrong_shape_raises(self):
        path = write_export("just a string")
        with self.assertRaises(ValueError):
            load_web_export(path)



class TestAttachments(unittest.TestCase):
    """A message that was only an uploaded file still carries its text."""

    def test_attachment_text_used_as_last_resort(self):
        m = msg("human", text="", content=[])
        m["attachments"] = [{"file_name": "notes.txt",
                             "extracted_content": "the actual pasted content"}]
        s = parse_conversation(conversation([m]))
        self.assertEqual(s.prompts, ["[attachment: notes.txt] the actual pasted content"])

    def test_long_attachment_is_excerpted_not_inlined(self):
        m = msg("human", text="", content=[])
        m["attachments"] = [{"file_name": "big.csv", "extracted_content": "x" * 50000}]
        s = parse_conversation(conversation([m]))
        self.assertLess(len(s.prompts[0]), 400)
        self.assertTrue(s.prompts[0].endswith("…"))

    def test_empty_attachment_yields_nothing(self):
        m = msg("human", text="", content=[])
        m["attachments"] = [{"file_name": "", "extracted_content": ""}]
        self.assertEqual(parse_conversation(conversation([m])).prompts, [])

    def test_real_text_wins_over_attachment(self):
        m = msg("human", "what I typed")
        m["attachments"] = [{"file_name": "a.txt", "extracted_content": "file body"}]
        self.assertEqual(parse_conversation(conversation([m])).prompts, ["what I typed"])
if __name__ == "__main__":
    unittest.main(verbosity=2)
