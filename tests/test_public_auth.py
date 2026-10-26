from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from ppm import db
from ppm.auth import (
    MAX_FAILED_LOGINS,
    accept_invite,
    create_invite,
    create_session,
    create_user,
    hash_password,
    login,
    resolve_session,
    revoke_session,
    verify_password,
)

pytestmark = pytest.mark.db


def test_scrypt_and_legacy_hashes_verify():
    legacy = hash_password("secret")
    assert verify_password("secret", legacy)
    assert not verify_password("wrong", legacy)

    from ppm.auth import hash_password_scrypt

    secure = hash_password_scrypt("secret")
    assert secure.startswith("scrypt$")
    assert verify_password("secret", secure)
    assert not verify_password("wrong", secure)


def test_invite_login_session_and_revoke(clean_db):
    code = create_invite("junior", email="new@example.com")
    user = accept_invite(code, "newuser", "New User", "correct horse battery")
    assert user["role"] == "junior"

    with pytest.raises(ValueError):
        accept_invite(code, "other", "Other", "another password")

    authenticated, token, error = login("newuser", "correct horse battery")
    assert error is None
    assert authenticated["username"] == "newuser"
    assert token

    resolved = resolve_session(token)
    assert resolved and resolved["user_id"] == user["user_id"]
    raw = db.fetch_one(
        "SELECT token_hash FROM user_session WHERE user_id = %s", (user["user_id"],)
    )["token_hash"]
    assert raw != token
    assert len(raw) == 64

    revoke_session(token)
    assert resolve_session(token) is None


def test_expired_invite_is_refused(clean_db):
    code = create_invite("junior", email="late@example.com", expires_hours=-1)
    with pytest.raises(ValueError, match="expired"):
        accept_invite(code, "late", "Late", "a long enough password")


def test_login_lockout(clean_db):
    create_user("locked", "Locked", "junior", "right password", secure_hash=True)
    for _ in range(MAX_FAILED_LOGINS):
        user, token, error = login("locked", "wrong password")
        assert user is None and token is None
    assert error == "locked"
    user, token, error = login("locked", "right password")
    assert user is None and token is None and error == "locked"


def test_public_mode_disables_seed_accounts(clean_db, monkeypatch):
    monkeypatch.setenv("PPM_PUBLIC_MODE", "1")
    from ppm.auth import ensure_seed_users

    assert ensure_seed_users() == []
    assert db.fetch_one("SELECT count(*) AS n FROM app_user")["n"] == 0


def test_session_expiry_is_not_returned(clean_db):
    user = create_user("expired", "Expired", "junior", "password", secure_hash=True)
    token = create_session(user["user_id"], days=1)
    db.execute(
        "UPDATE user_session SET expires_at = %s WHERE user_id = %s",
        (datetime(2000, 1, 1, tzinfo=timezone.utc), user["user_id"]),
    )
    assert resolve_session(token) is None
