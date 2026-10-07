"""Migration runner: applies migrations/*.sql in filename order and records
checksums in schema_migration. A changed, already-applied file is an error -
the schema history is append-only."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ppm import db
from ppm.config import REPO_ROOT

MIGRATIONS_DIR = REPO_ROOT / "migrations"

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migration (
    filename    text PRIMARY KEY,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


def migrate(migrations_dir: Path | None = None, dsn: str | None = None) -> list[str]:
    """Apply pending migrations; returns the filenames applied in this run."""
    directory = migrations_dir or MIGRATIONS_DIR
    if not directory.is_dir():
        raise FileNotFoundError(f"no migrations directory at {directory}")

    db.execute(_BOOTSTRAP)
    applied = {row["filename"]: row["checksum"] for row in db.fetch_all("SELECT filename, checksum FROM schema_migration")}
    newly_applied: list[str] = []

    for path in sorted(directory.glob("*.sql")):
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.name in applied:
            if applied[path.name] != checksum:
                raise RuntimeError(
                    f"{path.name} changed after it was applied - migrations are append-only; "
                    "add a new file instead"
                )
            continue
        sql = path.read_text(encoding="utf-8")
        with db.connection() as conn:
            conn.execute(sql)
            conn.execute(
                "INSERT INTO schema_migration (filename, checksum) VALUES (%s, %s)",
                (path.name, checksum),
            )
        newly_applied.append(path.name)

    return newly_applied
