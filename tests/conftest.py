"""Test configuration. Tests run against the ppm_test database; the env var
is set before any ppm import so .env cannot leak the dev database in."""

from __future__ import annotations

import os
from pathlib import Path

os.environ["PPM_DSN"] = "postgresql://ppm:ppm_dev_password@127.0.0.1:5432/ppm_test"
os.environ["PPM_DSN_APP"] = "postgresql://ppm_app:ppm_app_dev_password@127.0.0.1:5432/ppm_test"

import pytest  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"


def _db_up() -> bool:
    dsn = os.environ["PPM_DSN"]
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3):
            return True
    except Exception:
        return False


DB_UP = _db_up()
requires_db = pytest.mark.skipif(not DB_UP, reason="postgres with pgvector is not reachable")


@pytest.fixture(scope="session")
def settings():
    from ppm.config import get_settings

    return get_settings()


@pytest.fixture(scope="session")
def registry():
    from ppm.registry import get_registry

    return get_registry()


@pytest.fixture()
def stub_settings(settings):
    from dataclasses import replace

    return replace(settings, provider="stub")


@pytest.fixture(scope="session")
def samples() -> dict[str, Path]:
    """Ensure the synthetic sample workbooks exist."""
    alpha = SAMPLES_DIR / "synthetic_alpha_elemental.xlsx"
    if not alpha.exists():
        import sys

        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import make_sample_data

        make_sample_data.main()
    return {
        "alpha": SAMPLES_DIR / "synthetic_alpha_elemental.xlsx",
        "beta": SAMPLES_DIR / "synthetic_beta_tender.xlsx",
        "gamma": SAMPLES_DIR / "synthetic_gamma_detailed.xlsx",
    }


@pytest.fixture(scope="session")
def prepared_db(settings):
    if not DB_UP:
        pytest.skip("postgres with pgvector is not reachable")
    from langgraph.checkpoint.postgres import PostgresSaver

    from ppm.migrate import migrate

    migrate()
    with PostgresSaver.from_conn_string(settings.dsn) as saver:
        saver.setup()
    return settings.dsn


@pytest.fixture()
def clean_db(prepared_db):
    from ppm import db

    db.execute(
        "TRUNCATE benchmark_rate, cost_plan, document, project, ingest_run, search_log, document_chunk, "
        "lesson_weight_log, lesson_learned, person_competency, competency_standard, private_memory, "
        "action_run, person, deliverable, report_run, change_event, risk RESTART IDENTITY CASCADE"
    )
    yield
    db.reset_pool()
