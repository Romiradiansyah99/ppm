from __future__ import annotations

import json

import pytest

from ppm.analyst.memory import list_memories
from ppm.auth import create_user
from ppm.conversations import create_conversation
from ppm.mcp import domain
from ppm.tool_context import issue

pytestmark = pytest.mark.db


def test_mcp_tools_verify_context_and_scope(clean_db, monkeypatch):
    secret = "mcp-test-secret"
    monkeypatch.setenv("PPM_TOOL_SECRET", secret)
    monkeypatch.setenv("PPM_ACCESS_LEVEL", "baseline")

    user = create_user("mcpuser", "MCP User", "junior", "a long enough password", secure_hash=True)
    conversation = create_conversation(user["user_id"])
    token = issue(user, conversation["conversation_id"], secret)

    saved = json.loads(domain.remember_preference(token, "Prefers concise answers.", "preference"))
    assert saved["saved"] is True
    assert list_memories(user["user_id"])[0]["content"] == "Prefers concise answers."

    stats = json.loads(domain.corpus_stats(token))
    assert "documents" in stats

    tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    assert "error" in json.loads(domain.corpus_stats(tampered))

    senior = create_user("mcpsenior", "MCP Senior", "senior_manager", "a long enough password", secure_hash=True)
    senior_thread = create_conversation(senior["user_id"])
    senior_token = issue(senior, senior_thread["conversation_id"], secret)
    assert "wrong access scope" in json.loads(domain.corpus_stats(senior_token))["error"]
