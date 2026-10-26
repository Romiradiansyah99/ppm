"""Persistent per-user conversations (threads) and messages.

The thread ID is generated server-side and stored with the owner. The browser
never gets to invent or reuse another user's thread.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ppm import db


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _conversation(row: dict) -> dict:
    return {
        "conversation_id": str(row["conversation_id"]),
        "thread_id": row["thread_id"],
        "user_id": str(row["user_id"]),
        "hermes_session_id": row.get("hermes_session_id"),
        "title": row["title"],
        "summary": row.get("summary"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_conversation(user_id: str, title: str = "New conversation") -> dict:
    row = db.fetch_one(
        "INSERT INTO conversation (thread_id, user_id, title) "
        "VALUES (%s, %s, %s) RETURNING *",
        (uuid4().hex, user_id, (title or "New conversation")[:120]),
    )
    return _conversation(row)


def get_conversation(user_id: str, *, thread_id: str | None = None,
                     conversation_id: str | None = None) -> dict | None:
    if conversation_id:
        row = db.fetch_one(
            "SELECT * FROM conversation WHERE conversation_id = %s AND user_id = %s",
            (conversation_id, user_id),
        )
    elif thread_id:
        row = db.fetch_one(
            "SELECT * FROM conversation WHERE thread_id = %s AND user_id = %s",
            (thread_id, user_id),
        )
    else:
        return None
    return _conversation(row) if row else None


def get_last_conversation(user_id: str) -> dict | None:
    row = db.fetch_one(
        "SELECT * FROM conversation WHERE user_id = %s AND archived_at IS NULL "
        "ORDER BY updated_at DESC LIMIT 1",
        (user_id,),
    )
    return _conversation(row) if row else None


def list_conversations(user_id: str, limit: int = 40) -> list[dict]:
    rows = db.fetch_all(
        "SELECT c.*, "
        "  (SELECT m.content FROM chat_message m WHERE m.conversation_id = c.conversation_id "
        "   ORDER BY m.created_at DESC LIMIT 1) AS last_message, "
        "  (SELECT count(*) FROM chat_message m WHERE m.conversation_id = c.conversation_id) AS message_count "
        "FROM conversation c WHERE c.user_id = %s AND c.archived_at IS NULL "
        "ORDER BY c.updated_at DESC LIMIT %s",
        (user_id, limit),
    )
    result = []
    for row in rows:
        item = _conversation(row)
        item["last_message"] = row.get("last_message")
        item["message_count"] = row.get("message_count")
        result.append(item)
    return result


def load_messages(conversation_id: str, limit: int = 20) -> list[dict]:
    rows = db.fetch_all(
        "SELECT message_id, role, content, events, usage, created_at "
        "FROM chat_message WHERE conversation_id = %s "
        "ORDER BY created_at DESC LIMIT %s",
        (conversation_id, limit),
    )
    rows.reverse()
    return [
        {
            "message_id": str(row["message_id"]),
            "role": row["role"],
            "content": row["content"],
            "events": row.get("events") or [],
            "usage": row.get("usage") or {},
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def add_message(
    conversation_id: str,
    role: str,
    content: str,
    *,
    events: list[dict] | None = None,
    usage: dict | None = None,
) -> dict:
    if role not in {"user", "assistant", "system", "tool"}:
        raise ValueError("invalid message role")
    row = db.fetch_one(
        "INSERT INTO chat_message (conversation_id, role, content, events, usage) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING message_id, created_at",
        (conversation_id, role, content, json.dumps(events or []), json.dumps(usage or {})),
    )
    db.execute(
        "UPDATE conversation SET updated_at = now() WHERE conversation_id = %s",
        (conversation_id,),
    )
    if role == "user":
        db.execute(
            "UPDATE conversation SET title = %s WHERE conversation_id = %s "
            "AND title = 'New conversation'",
            (content.strip().replace("\n", " ")[:80] or "New conversation", conversation_id),
        )
    return {"message_id": str(row["message_id"]), "created_at": row["created_at"]}


def set_hermes_session(conversation_id: str, session_id: str | None) -> None:
    db.execute(
        "UPDATE conversation SET hermes_session_id = %s, updated_at = now() "
        "WHERE conversation_id = %s",
        (session_id, conversation_id),
    )


def set_summary(conversation_id: str, summary: str) -> None:
    db.execute(
        "UPDATE conversation SET summary = %s, updated_at = now() WHERE conversation_id = %s",
        (summary[:8000], conversation_id),
    )


def archive_conversation(user_id: str, thread_id: str) -> bool:
    changed = db.execute(
        "UPDATE conversation SET archived_at = now() "
        "WHERE thread_id = %s AND user_id = %s AND archived_at IS NULL",
        (thread_id, user_id),
    )
    return changed > 0


def delete_conversation(user_id: str, thread_id: str) -> bool:
    changed = db.execute(
        "DELETE FROM conversation WHERE thread_id = %s AND user_id = %s",
        (thread_id, user_id),
    )
    return changed > 0


def count_user_messages_today(user_id: str) -> int:
    row = db.fetch_one(
        "SELECT count(*) AS count FROM chat_message m "
        "JOIN conversation c ON c.conversation_id = m.conversation_id "
        "WHERE c.user_id = %s AND m.role = 'user' "
        "AND m.created_at >= date_trunc('day', now())",
        (user_id,),
    )
    return int(row["count"] if row else 0)


def conversation_owner(conversation_id: str) -> str | None:
    row = db.fetch_one(
        "SELECT user_id FROM conversation WHERE conversation_id = %s",
        (conversation_id,),
    )
    return str(row["user_id"]) if row else None


def transcript_for_prompt(messages: list[dict]) -> list[dict[str, Any]]:
    """Only user/assistant text is replayed to Hermes. Tool payloads stay in PPM."""
    return [
        {"role": item["role"], "content": item["content"]}
        for item in messages
        if item["role"] in {"user", "assistant"} and item.get("content")
    ]
