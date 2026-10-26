"""Accounts, invites, login sessions and audit for the public app.

Passwords use stdlib scrypt for new accounts. Existing PBKDF2 hashes remain
verifiable and are upgraded on a successful login. Hermes never owns user
identity or memory; this module and Postgres do.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from ppm import db

PBKDF2_ITERATIONS = 200_000
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
ROLES = {"senior_manager", "junior"}
MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str, salt: str | None = None, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Legacy-compatible PBKDF2 hash used by dev seeds."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def hash_password_scrypt(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt.encode("utf-8"),
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        parts = stored.split("$")
    except ValueError:
        return False
    if not parts:
        return False
    if parts[0] == "scrypt" and len(parts) == 6:
        try:
            n, r, p, salt, expected = int(parts[1]), int(parts[2]), int(parts[3]), parts[4], parts[5]
        except (ValueError, TypeError):
            return False
        try:
            digest = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt.encode("utf-8"),
                n=n,
                r=r,
                p=p,
                dklen=len(bytes.fromhex(expected)),
            ).hex()
        except (ValueError, MemoryError):
            return False
        return hmac.compare_digest(digest, expected)
    if parts[0] == "pbkdf2_sha256" and len(parts) == 4:
        try:
            iterations = int(parts[1])
        except ValueError:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), parts[2].encode("utf-8"), iterations
        ).hex()
        return hmac.compare_digest(digest, parts[3])
    return False


def access_level(role: str) -> str:
    return "all" if role == "senior_manager" else "baseline"


def _public(row: dict) -> dict:
    return {
        "user_id": str(row["user_id"]),
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "access_level": access_level(row["role"]),
    }


def get_user(user_id: str) -> dict | None:
    row = db.fetch_one("SELECT * FROM app_user WHERE user_id = %s AND disabled_at IS NULL", (user_id,))
    return _public(row) if row else None


def list_users() -> list[dict]:
    rows = db.fetch_all(
        "SELECT * FROM app_user WHERE disabled_at IS NULL ORDER BY role, username"
    )
    return [_public(row) for row in rows]


def create_user(
    username: str,
    display_name: str,
    role: str,
    password: str,
    *,
    secure_hash: bool = False,
) -> dict:
    if role not in ROLES:
        raise ValueError(f"role must be one of {sorted(ROLES)}")
    if not username.strip() or not password:
        raise ValueError("username and password are required")
    stored = hash_password_scrypt(password) if secure_hash else hash_password(password)
    row = db.fetch_one(
        "INSERT INTO app_user (username, display_name, password_hash, role) "
        "VALUES (%s, %s, %s, %s) RETURNING *",
        (username.strip(), display_name.strip() or username.strip(), stored, role),
    )
    return _public(row)


def authenticate(username: str, password: str) -> dict | None:
    row = db.fetch_one(
        "SELECT * FROM app_user WHERE lower(username) = lower(%s) AND disabled_at IS NULL",
        (username.strip(),),
    )
    if not row or not verify_password(password, row["password_hash"]):
        return None
    return _public(row)


def record_login_attempt(username: str, success: bool, ip_hash: str | None = None) -> None:
    db.execute(
        "INSERT INTO login_attempt (username, ip_hash, success) VALUES (%s, %s, %s)",
        (username.strip()[:200], ip_hash, success),
    )


def login(
    username: str,
    password: str,
    *,
    ip_hash: str | None = None,
    user_agent: str | None = None,
    days: int = 30,
) -> tuple[dict | None, str | None, str | None]:
    """Return (user, session_token, error_code)."""
    username = username.strip()
    row = db.fetch_one(
        "SELECT * FROM app_user WHERE lower(username) = lower(%s)", (username,)
    )
    if not row or row.get("disabled_at") is not None:
        record_login_attempt(username, False, ip_hash)
        return None, None, "invalid"
    locked_until = row.get("locked_until")
    if locked_until and locked_until > _now():
        record_login_attempt(username, False, ip_hash)
        return None, None, "locked"
    if not verify_password(password, row["password_hash"]):
        failures = int(row.get("failed_attempts") or 0) + 1
        lock = _now() + timedelta(minutes=LOCK_MINUTES) if failures >= MAX_FAILED_LOGINS else None
        db.execute(
            "UPDATE app_user SET failed_attempts = %s, locked_until = %s WHERE user_id = %s",
            (failures, lock, row["user_id"]),
        )
        record_login_attempt(username, False, ip_hash)
        return None, None, "locked" if lock else "invalid"

    # Upgrade a legacy PBKDF2 hash now that the password is known.
    if str(row["password_hash"]).startswith("pbkdf2_sha256$"):
        db.execute(
            "UPDATE app_user SET password_hash = %s WHERE user_id = %s",
            (hash_password_scrypt(password), row["user_id"]),
        )
    db.execute(
        "UPDATE app_user SET failed_attempts = 0, locked_until = NULL, last_login_at = now() "
        "WHERE user_id = %s",
        (row["user_id"],),
    )
    token = create_session(row["user_id"], days=days, user_agent=user_agent, ip_hash=ip_hash)
    record_login_attempt(username, True, ip_hash)
    audit(str(row["user_id"]), "login", target=username, ip_hash=ip_hash)
    return _public(row), token, None


def create_session(
    user_id: str,
    *,
    days: int = 30,
    user_agent: str | None = None,
    ip_hash: str | None = None,
) -> str:
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO user_session (user_id, token_hash, expires_at, user_agent, ip_hash) "
        "VALUES (%s, %s, %s, %s, %s)",
        (
            user_id,
            _token_hash(token),
            _now() + timedelta(days=days),
            (user_agent or "")[:500] or None,
            ip_hash,
        ),
    )
    return token


def resolve_session(token: str | None) -> dict | None:
    if not token:
        return None
    row = db.fetch_one(
        "SELECT s.session_id, s.expires_at, s.revoked_at, u.* "
        "FROM user_session s JOIN app_user u ON u.user_id = s.user_id "
        "WHERE s.token_hash = %s AND s.revoked_at IS NULL AND u.disabled_at IS NULL",
        (_token_hash(token),),
    )
    if not row or row["expires_at"] <= _now():
        return None
    db.execute("UPDATE user_session SET last_seen_at = now() WHERE session_id = %s", (row["session_id"],))
    user = _public(row)
    user["session_id"] = str(row["session_id"])
    return user


def revoke_session(token: str | None) -> None:
    if token:
        db.execute(
            "UPDATE user_session SET revoked_at = now() WHERE token_hash = %s",
            (_token_hash(token),),
        )


def revoke_user_sessions(user_id: str) -> int:
    return db.execute(
        "UPDATE user_session SET revoked_at = now() "
        "WHERE user_id = %s AND revoked_at IS NULL",
        (user_id,),
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_invite(
    role: str,
    *,
    email: str | None = None,
    created_by: str | None = None,
    expires_hours: int = 72,
) -> str:
    if role not in ROLES:
        raise ValueError(f"role must be one of {sorted(ROLES)}")
    code = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO invite (email, role, token_hash, expires_at, created_by) "
        "VALUES (%s, %s, %s, %s, %s)",
        (
            (email or "").strip() or None,
            role,
            _token_hash(code),
            _now() + timedelta(hours=expires_hours),
            created_by,
        ),
    )
    return code


def accept_invite(code: str, username: str, display_name: str, password: str) -> dict:
    invite = db.fetch_one(
        "SELECT * FROM invite WHERE token_hash = %s AND accepted_at IS NULL",
        (_token_hash(code.strip()),),
    )
    if not invite:
        raise ValueError("invite is invalid or already used")
    if invite["expires_at"] <= _now():
        raise ValueError("invite has expired")
    if db.fetch_one("SELECT 1 FROM app_user WHERE lower(username) = lower(%s)", (username.strip(),)):
        raise ValueError("username is already taken")
    user = create_user(
        username.strip(),
        display_name.strip() or username.strip(),
        invite["role"],
        password,
        secure_hash=True,
    )
    db.execute("UPDATE invite SET accepted_at = now() WHERE invite_id = %s", (invite["invite_id"],))
    audit(user["user_id"], "invite.accepted", target=invite["email"])
    return user


def bootstrap_admin(username: str, display_name: str, password: str) -> dict:
    existing = db.fetch_one("SELECT count(*) AS count FROM app_user")["count"]
    if existing:
        raise ValueError("users already exist; use ppm user invite instead")
    user = create_user(username, display_name, "senior_manager", password, secure_hash=True)
    audit(user["user_id"], "bootstrap.admin", target=username)
    return user


def ensure_seed_users() -> list[dict]:
    """Create dev-only accounts. Never runs in public mode or with a password env unset."""
    if os.environ.get("PPM_PUBLIC_MODE", "").strip().lower() in {"1", "true", "yes", "on"}:
        return []
    existing = db.fetch_one("SELECT count(*) AS count FROM app_user")["count"]
    if existing:
        return []
    created = []
    created.append(create_user(
        os.environ.get("PPM_SEED_SENIOR_USER", "senior"),
        "Senior Manager",
        "senior_manager",
        os.environ.get("PPM_SEED_SENIOR_PASSWORD", "senior123"),
    ))
    created.append(create_user(
        os.environ.get("PPM_SEED_JUNIOR_USER", "junior"),
        "Junior Analyst",
        "junior",
        os.environ.get("PPM_SEED_JUNIOR_PASSWORD", "junior123"),
    ))
    return created


def set_password(username: str, password: str) -> bool:
    changed = db.execute(
        "UPDATE app_user SET password_hash = %s, failed_attempts = 0, locked_until = NULL "
        "WHERE lower(username) = lower(%s)",
        (hash_password_scrypt(password), username),
    )
    return changed > 0


def audit(
    user_id: str | None,
    action: str,
    *,
    target: str | None = None,
    detail: dict[str, Any] | None = None,
    ip_hash: str | None = None,
) -> None:
    db.execute(
        "INSERT INTO audit_event (user_id, action, target, detail, ip_hash) "
        "VALUES (%s, %s, %s, %s, %s)",
        (user_id, action[:100], (target or "")[:500] or None, json.dumps(detail or {}), ip_hash),
    )
