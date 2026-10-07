"""Device-account auth: scrypt hashing, verification, account lifecycle."""

from __future__ import annotations

from ppm import auth
from tests.conftest import requires_db


def test_password_hash_roundtrip_and_salt():
    stored = auth.hash_password("correct horse battery")
    assert stored.startswith("scrypt$")
    assert auth.verify_password("correct horse battery", stored)
    assert not auth.verify_password("wrong password", stored)
    assert not auth.verify_password("x", "garbage")
    assert auth.hash_password("same") != auth.hash_password("same")     # per-hash salt


@requires_db
def test_account_lifecycle(clean_db):
    person_id = auth.create_person("alice", "Alice", "pw123", role="senior_consultant")
    person = auth.authenticate("alice", "pw123")
    assert person is not None and str(person["person_id"]) == person_id
    assert person["role"] == "senior_consultant"
    assert auth.authenticate("alice", "nope") is None
    assert auth.authenticate("bob", "pw123") is None

    assert auth.set_password("alice", "newpw")
    assert auth.authenticate("alice", "newpw") is not None
    assert auth.authenticate("alice", "pw123") is None
    assert not auth.set_password("nobody", "x")


@requires_db
def test_username_case_insensitive(clean_db):
    auth.create_person("MixedCase", "M", "pw")
    assert auth.get_person_by_username("mixedcase") is not None
    assert auth.authenticate("MIXEDCASE", "pw") is not None
