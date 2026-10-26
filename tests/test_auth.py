import pytest

from ppm.auth import (
    access_level,
    authenticate,
    ensure_seed_users,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip():
    stored = hash_password("secret")
    assert stored.startswith("pbkdf2_sha256$")
    assert verify_password("secret", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("secret", "broken")


@pytest.mark.db
def test_seed_and_authenticate(clean_db):
    created = ensure_seed_users()
    assert {user["username"] for user in created} == {"senior", "junior"}

    senior = authenticate("senior", "senior123")
    junior = authenticate("junior", "junior123")
    assert senior["role"] == "senior_manager"
    assert junior["role"] == "junior"
    assert access_level(senior["role"]) == "all"
    assert access_level(junior["role"]) == "baseline"
    assert authenticate("senior", "wrong") is None
    assert authenticate("nobody", "x") is None

    # Seeding is idempotent.
    assert ensure_seed_users() == []
