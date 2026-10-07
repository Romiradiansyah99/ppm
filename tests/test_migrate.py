"""Migration ledger behaviour against the test database."""

from __future__ import annotations

from ppm.migrate import migrate
from tests.conftest import requires_db


@requires_db
def test_migrations_idempotent(prepared_db):
    assert migrate() == []   # already applied by the session fixture


@requires_db
def test_schema_migration_ledger(prepared_db):
    from ppm import db

    rows = db.fetch_all("SELECT filename, checksum FROM schema_migration ORDER BY filename")
    assert rows and rows[0]["filename"] == "0001_init.sql"
    assert len(rows[0]["checksum"]) == 64
