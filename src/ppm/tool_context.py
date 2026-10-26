"""Signed, short-lived context for PPM MCP tool calls.

Hermes is a shared private service per role, so a tool cannot rely on ambient
process identity for the current web user. The Streamlit app issues a token
bound to user, conversation and access level; every MCP tool verifies it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from ppm import db
from ppm.auth import access_level


class ContextError(ValueError):
    """The tool context token is missing, expired or not owned by the user."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue(user: dict, conversation_id: str, secret: str, ttl_seconds: int = 900) -> str:
    if not secret:
        raise ContextError("PPM_TOOL_SECRET is not configured")
    payload = {
        "u": str(user["user_id"]),
        "c": str(conversation_id),
        "r": str(user.get("access_level") or access_level(user["role"])),
        "e": int(time.time()) + max(60, ttl_seconds),
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _b64(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify(token: str, secret: str, *, required_access: str | None = None) -> dict[str, Any]:
    if not token or not secret:
        raise ContextError("tool context is missing")
    try:
        body, signature = token.split(".", 1)
    except ValueError as exc:
        raise ContextError("tool context is malformed") from exc
    expected = _b64(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise ContextError("tool context signature is invalid")
    try:
        payload = json.loads(_unb64(body).decode("utf-8"))
    except Exception as exc:
        raise ContextError("tool context payload is invalid") from exc
    if int(payload.get("e", 0)) < int(time.time()):
        raise ContextError("tool context has expired")
    if required_access and payload.get("r") != required_access:
        raise ContextError("tool context has the wrong access scope")

    row = db.fetch_one(
        "SELECT c.conversation_id, c.user_id, u.username, u.display_name, u.role "
        "FROM conversation c JOIN app_user u ON u.user_id = c.user_id "
        "WHERE c.conversation_id = %s AND c.user_id = %s AND u.disabled_at IS NULL",
        (payload.get("c"), payload.get("u")),
    )
    if not row:
        raise ContextError("tool context conversation is not owned by this user")
    if payload.get("r") != access_level(row["role"]):
        raise ContextError("tool context access does not match the user's role")
    return {
        "user_id": str(row["user_id"]),
        "conversation_id": str(row["conversation_id"]),
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "access_level": access_level(row["role"]),
    }
