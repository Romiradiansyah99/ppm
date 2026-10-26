"""Per-user long-term memory and explicit preferences.

Memory is intentionally simple and auditable: short facts in a SQL table,
key/value preferences in another, both keyed by user_id. It is never shared
between users and never delegated to Hermes' built-in profile memory.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ppm import db
from ppm.text import literal_terms

SECRET_RE = re.compile(
    r"(sk-[a-z0-9]{8,}|api[_-]?key|password|passwd|secret|bearer\s+[a-z0-9])",
    re.IGNORECASE,
)
PREF_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,48}$")
MEMORY_HINTS = (
    "ingat", "selalu", "jangan", "prefer", "suka", "pakai", "gunakan",
    "format", "singkat", "detail", "tabel", "bahasa", "mata uang",
    "currency", "area basis", "remember", "always", "never", "prefer",
    "brief", "table", "language", "spelling", "british", "uk",
)


def _clean(content: str) -> str:
    return " ".join(content.split()).strip()


def _reject_secret(content: str) -> None:
    if SECRET_RE.search(content):
        raise ValueError("refusing to store secrets or credentials in memory")


def remember(
    user_id: str,
    content: str,
    kind: str = "fact",
    source_run_id: str | None = None,
    conversation_id: str | None = None,
    source: str = "user",
) -> str | None:
    content = _clean(content)
    if not content:
        return None
    if kind not in {"fact", "preference", "project_context", "correction"}:
        kind = "fact"
    _reject_secret(content)
    content = content[:1000]
    existing = db.fetch_one(
        "SELECT memory_id FROM user_memory WHERE user_id = %s AND content = %s LIMIT 1",
        (user_id, content),
    )
    if existing:
        db.execute(
            "UPDATE user_memory SET updated_at = now(), kind = %s, source = %s "
            "WHERE memory_id = %s",
            (kind, source, existing["memory_id"]),
        )
        return str(existing["memory_id"])
    row = db.fetch_one(
        "INSERT INTO user_memory (user_id, kind, content, source_run_id, conversation_id, source) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING memory_id",
        (user_id, kind, content, source_run_id, conversation_id, source),
    )
    return str(row["memory_id"])


def set_preference(
    user_id: str,
    key: str,
    value: str,
    *,
    source: str = "explicit",
) -> None:
    key = _clean(key).lower()
    value = _clean(value)
    if not PREF_KEY_RE.fullmatch(key):
        raise ValueError("preference key must be a short lower-case identifier")
    if not value:
        raise ValueError("preference value is empty")
    _reject_secret(value)
    db.execute(
        "INSERT INTO user_preference (user_id, pref_key, pref_value, source) "
        "VALUES (%s, %s, %s, %s) ON CONFLICT (user_id, pref_key) DO UPDATE SET "
        "pref_value = EXCLUDED.pref_value, source = EXCLUDED.source, updated_at = now()",
        (user_id, key, value[:1000], source),
    )


def list_preferences(user_id: str) -> list[dict[str, Any]]:
    return db.fetch_all(
        "SELECT pref_key, pref_value, source, updated_at FROM user_preference "
        "WHERE user_id = %s ORDER BY pref_key",
        (user_id,),
    )


def delete_preference(user_id: str, key: str) -> int:
    return db.execute(
        "DELETE FROM user_preference WHERE user_id = %s AND pref_key = %s",
        (user_id, key),
    )


def list_memories(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    return db.fetch_all(
        "SELECT memory_id, kind, content, source_run_id, conversation_id, source, created_at, updated_at "
        "FROM user_memory WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
        (user_id, limit),
    )


def recall(user_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
    terms = literal_terms(query, limit=5)
    if terms:
        clauses = " OR ".join("content ILIKE %s" for _ in terms)
        params: list[Any] = [user_id, *[f"%{term}%" for term in terms], limit]
        return db.fetch_all(
            f"SELECT memory_id, kind, content, created_at FROM user_memory "
            f"WHERE user_id = %s AND ({clauses}) ORDER BY created_at DESC LIMIT %s",
            params,
        )
    return list_memories(user_id, limit=limit)


def count(user_id: str) -> int:
    return db.fetch_one(
        "SELECT count(*) AS count FROM user_memory WHERE user_id = %s", (user_id,)
    )["count"]


def clear(user_id: str) -> int:
    memories = db.execute("DELETE FROM user_memory WHERE user_id = %s", (user_id,))
    preferences = db.execute("DELETE FROM user_preference WHERE user_id = %s", (user_id,))
    return memories + preferences


def delete_memory(user_id: str, memory_id: str) -> int:
    return db.execute(
        "DELETE FROM user_memory WHERE user_id = %s AND memory_id = %s",
        (user_id, memory_id),
    )


def prune(days: int = 365) -> int:
    return db.execute(
        "DELETE FROM user_memory WHERE created_at < now() - (%s || ' days')::interval",
        (days,),
    )


def memory_context(
    user_id: str,
    query: str | None = None,
    limit: int = 8,
    include_recall: bool = True,
) -> str:
    """Preferences first, then memories relevant to the current question."""
    lines: list[str] = []
    for row in list_preferences(user_id):
        lines.append(f"- [preference:{row['pref_key']}] {row['pref_value']}")

    preferences = db.fetch_all(
        "SELECT content FROM user_memory WHERE user_id = %s AND kind = 'preference' "
        "ORDER BY created_at DESC LIMIT 5",
        (user_id,),
    )
    relevant = recall(user_id, query, limit=limit) if query and include_recall else []
    recent = list_memories(user_id, limit=limit)

    seen: set[str] = set()
    for row in preferences:
        content = row["content"]
        if content not in seen:
            lines.append(f"- [preference] {content}")
            seen.add(content)
    for row in [*relevant, *recent]:
        content = row["content"]
        if content not in seen:
            lines.append(f"- [{row['kind']}] {content}")
            seen.add(content)
    return "\n".join(lines) or "(no saved memory)"


def should_extract(prompt: str) -> bool:
    lowered = prompt.lower()
    return any(hint in lowered for hint in MEMORY_HINTS)


def extract_memories(
    user: dict,
    conversation: str,
    settings,
    conversation_id: str | None = None,
) -> list[str]:
    """Extract durable facts from a turn and store them for this user."""
    try:
        from ppm.analyst.codegen import OpenAICompatibleCodegen

        client = OpenAICompatibleCodegen(settings)
        prompt = (
            "Extract user preferences and durable facts from the conversation.\n"
            "Preferences: how this user likes answers, naming, currency, area basis, "
            "format, level of detail, or recurring definitions.\n"
            "Facts: project context, numbers, constraints, corrections.\n"
            "Ignore small talk. Return JSON: "
            "{\"preferences\": [\"...\"], \"facts\": [\"...\"]}. "
            "Max 3 each, one sentence each. "
            "Never include passwords, API keys or secrets.\n\nConversation:\n"
            + conversation[:12000]
        )
        content = client._chat([{"role": "user", "content": prompt}], json_mode=True)
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1]) if start >= 0 and end > start else {}
        saved: list[str] = []
        for kind, key in (("preference", "preferences"), ("fact", "facts")):
            for item in data.get(key, [])[:3]:
                if not isinstance(item, str):
                    continue
                try:
                    if remember(
                        user["user_id"], item, kind=kind,
                        conversation_id=conversation_id, source="extracted",
                    ):
                        saved.append(item)
                except ValueError:
                    continue
        return saved
    except Exception:
        return []
