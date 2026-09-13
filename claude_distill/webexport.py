"""Read the conversations.json that claude.ai emails you.

claude.ai chats live on Anthropic's servers and there is no API to fetch your own
history, so the only supported way to get them is Settings -> Privacy -> Export
data, which emails a ZIP containing conversations.json. This turns that file into
the same Session objects the transcript reader produces, so both sources render
identically and land in one vault.

Export schema (as of September 2026):

    [
      {
        "uuid": "...",
        "name": "conversation title",
        "created_at": "2026-09-01T10:00:00Z",
        "updated_at": "...",
        "chat_messages": [
          {
            "uuid": "...",
            "sender": "human" | "assistant",
            "text": "...",                        # may be empty
            "content": [{"type": "text", ...}],   # fallback when text is empty
            "created_at": "..."
          }
        ]
      }
    ]
"""

from __future__ import annotations

import io
import json
import os
from typing import Any

from .distill import CORRECTION_MARKERS, Session

__all__ = ["load_web_export", "parse_conversation"]


ATTACHMENT_EXCERPT = 300


def _message_text(message: dict) -> str:
    """Pull the text out of a message, whichever field it landed in.

    Three places, in order. Older exports put everything in `text`; newer ones
    sometimes leave that empty and use a `content` block list; and a message that
    was nothing but an uploaded file carries its text in `attachments`.

    Attachment text is excerpted rather than inlined. A pasted file can run to
    tens of thousands of characters, which is exactly the bulk this tool exists
    to remove - but knowing a file was shared, and which one, is signal.
    """
    text = (message.get("text") or "").strip()
    if text:
        return text

    parts = []
    for block in message.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    text = "\n".join(p for p in parts if p).strip()
    if text:
        return text

    for attachment in message.get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        extracted = (attachment.get("extracted_content") or "").strip()
        if not extracted:
            continue
        name = attachment.get("file_name") or "file"
        excerpt = " ".join(extracted.split())[:ATTACHMENT_EXCERPT]
        if len(extracted) > ATTACHMENT_EXCERPT:
            excerpt += "…"
        return f"[attachment: {name}] {excerpt}"

    return ""


def parse_conversation(conversation: dict) -> Session:
    """Convert one exported conversation into a Session."""
    session = Session(
        session_id=conversation.get("uuid", "") or "",
        source="claude-web",
        name=conversation.get("name") or "",
        cwd="claude.ai",
        started=conversation.get("created_at", "") or "",
        ended=conversation.get("updated_at", "") or "",
    )

    for message in conversation.get("chat_messages") or []:
        if not isinstance(message, dict):
            continue
        session.total_records += 1
        # Web exports have no tool calls, so "human" is unambiguous here - none
        # of the ambiguity that makes the .jsonl side hard applies.
        if message.get("sender") != "human":
            continue
        text = _message_text(message)
        if not text:
            continue
        session.prompts.append(text)
        if CORRECTION_MARKERS.search(text[:200]):
            session.corrections.append(text)

    return session


def load_web_export(path: str) -> list[Session]:
    """Load every conversation from an exported conversations.json.

    Accepts either the top-level list or a single conversation object, since
    people sometimes hand-extract one chat from the export.
    """
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        data: Any = json.load(fh)

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError(f"{os.path.basename(path)}: expected a list of conversations")

    sessions = []
    for conversation in data:
        if not isinstance(conversation, dict):
            continue
        try:
            sessions.append(parse_conversation(conversation))
        except Exception:
            # One malformed conversation must not lose the rest of the export.
            continue
    return sessions
