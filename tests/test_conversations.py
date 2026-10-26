from __future__ import annotations

import time

import pytest

from ppm import db
from ppm.analyst.memory import clear, list_memories, remember
from ppm.auth import create_user
from ppm.conversations import (
    add_message,
    archive_conversation,
    create_conversation,
    get_conversation,
    get_last_conversation,
    list_conversations,
    load_messages,
    set_hermes_session,
)
from ppm.tool_context import ContextError, issue, verify

pytestmark = pytest.mark.db


def _user(name: str) -> dict:
    return create_user(name, name.title(), "junior", "a sufficiently long password", secure_hash=True)


def test_threads_are_persisted_and_owned(clean_db):
    alice = _user("alice")
    bob = _user("bob")
    thread = create_conversation(alice["user_id"], "Cost review")
    add_message(thread["conversation_id"], "user", "What is the largest cost?")
    add_message(thread["conversation_id"], "assistant", "Preliminaries [S1].")
    set_hermes_session(thread["conversation_id"], "hs_123")

    same = get_conversation(alice["user_id"], thread_id=thread["thread_id"])
    assert same and same["hermes_session_id"] == "hs_123"
    assert get_conversation(bob["user_id"], thread_id=thread["thread_id"]) is None
    assert [item["thread_id"] for item in list_conversations(alice["user_id"])] == [thread["thread_id"]]
    assert list_conversations(bob["user_id"]) == []

    messages = load_messages(thread["conversation_id"])
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[0]["content"].startswith("What is")
    assert get_last_conversation(alice["user_id"])["thread_id"] == thread["thread_id"]

    assert archive_conversation(bob["user_id"], thread["thread_id"]) is False
    assert archive_conversation(alice["user_id"], thread["thread_id"]) is True
    assert list_conversations(alice["user_id"]) == []


def test_tool_context_binds_user_and_thread(clean_db, monkeypatch):
    alice = _user("alice")
    bob = _user("bob")
    thread = create_conversation(alice["user_id"])
    secret = "test-tool-secret"
    token = issue(alice, thread["conversation_id"], secret, ttl_seconds=60)
    ctx = verify(token, secret, required_access="baseline")
    assert ctx["user_id"] == alice["user_id"]
    assert ctx["conversation_id"] == thread["conversation_id"]

    other_thread = create_conversation(bob["user_id"])
    other_token = issue(bob, other_thread["conversation_id"], secret, ttl_seconds=60)
    with pytest.raises(ContextError):
        verify(other_token, secret, required_access="all")
    with pytest.raises(ContextError):
        verify(token + "x", secret, required_access="baseline")
    with pytest.raises(ContextError):
        verify(token, "wrong-secret", required_access="baseline")

    expired = issue(alice, thread["conversation_id"], secret, ttl_seconds=60)
    now = time.time()
    monkeypatch.setattr("ppm.tool_context.time.time", lambda: now + 3600)
    with pytest.raises(ContextError, match="expired"):
        verify(expired, secret, required_access="baseline")


def test_memory_is_per_user(clean_db):
    alice = _user("alice")
    bob = _user("bob")
    remember(alice["user_id"], "Prefers concise tables.", kind="preference")
    assert list_memories(alice["user_id"])
    assert list_memories(bob["user_id"]) == []
    clear(alice["user_id"])
    assert list_memories(alice["user_id"]) == []
