"""Device-account auth (plan section 6, Phase 2: no SSO yet).

Passwords are scrypt-hashed with a per-user salt; verification is constant
time. Sessions live in the Streamlit session / CLI invocations - the database
only ever stores the hash.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from ppm import db

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
KEY_LEN = 32


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=KEY_LEN)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=KEY_LEN)
    return hmac.compare_digest(candidate, expected)


def create_person(username: str, display_name: str, password: str, *, role: str = "consultant",
                  level: str | None = None, is_approver: bool = False) -> str:
    row = db.fetch_one(
        "INSERT INTO person (username, display_name, password_hash, role, level, is_approver) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING person_id",
        (username.strip().lower(), display_name, hash_password(password), role, level, is_approver),
    )
    return str(row["person_id"])


def get_person_by_username(username: str) -> dict | None:
    return db.fetch_one("SELECT * FROM person WHERE username = %s", (username.strip().lower(),))


def get_person(person_id: str) -> dict | None:
    return db.fetch_one("SELECT * FROM person WHERE person_id = %s", (person_id,))


def authenticate(username: str, password: str) -> dict | None:
    person = get_person_by_username(username)
    if person is None or not verify_password(password, person["password_hash"]):
        return None
    return person


def list_people() -> list[dict]:
    return db.fetch_all(
        "SELECT person_id, username, display_name, role, level, is_approver, created_at "
        "FROM person ORDER BY username"
    )


def set_password(username: str, password: str) -> bool:
    return db.execute(
        "UPDATE person SET password_hash = %s WHERE username = %s",
        (hash_password(password), username.strip().lower()),
    ) > 0
