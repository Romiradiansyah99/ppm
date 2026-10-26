from __future__ import annotations

import os

import pytest

TEST_DSN = os.environ.get(
    "PPM_TEST_DSN",
    "postgresql://ppm:ppm_dev_password@127.0.0.1:5432/ppm2_test",
)
os.environ["PPM_DSN"] = TEST_DSN


def _db_available() -> bool:
    try:
        import psycopg

        with psycopg.connect(TEST_DSN, connect_timeout=2):
            return True
    except Exception:
        return False


DB_AVAILABLE = _db_available()


def pytest_collection_modifyitems(config, items):
    if DB_AVAILABLE:
        return
    skip = pytest.mark.skip(reason="Postgres test database is not reachable")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def settings():
    from ppm.config import Settings, get_settings

    get_settings.cache_clear()
    base = get_settings()
    return Settings(
        dsn=TEST_DSN,
        dsn_test=TEST_DSN,
        residency=base.residency,
        ollama_host=base.ollama_host,
        chat_model=base.chat_model,
        embed_model=base.embed_model,
        embed_dim=base.embed_dim,
        num_gpu=base.num_gpu,
        num_ctx=base.num_ctx,
        top_k=base.top_k,
        min_similarity=base.min_similarity,
        user_label="test",
        manifest_path=base.manifest_path,
        taxonomy_path=base.taxonomy_path,
        app_specs_dir=base.app_specs_dir,
        llm_api_base=base.llm_api_base,
        llm_api_key=base.llm_api_key,
        llm_model=base.llm_model,
        llm_temperature=base.llm_temperature,
        analyst_max_parallel=base.analyst_max_parallel,
        analyst_task_timeout=base.analyst_task_timeout,
        analyst_allow_code_execution=base.analyst_allow_code_execution,
        analyst_egress_ack=base.analyst_egress_ack,
    )


@pytest.fixture()
def clean_db(settings):
    from ppm import db
    from ppm.migrate import migrate

    db.reset_pool()
    migrate()
    statements = (
        "TRUNCATE ingest_staging, ingest_run CASCADE",
        "TRUNCATE analysis_run CASCADE",
        "TRUNCATE teaching_event CASCADE",
        "TRUNCATE benchmark_case CASCADE",
        "TRUNCATE app_user CASCADE",
        "TRUNCATE project CASCADE",
        "TRUNCATE document CASCADE",
        "TRUNCATE template CASCADE",
    )
    for statement in statements:
        db.execute(statement)
    yield
    for statement in statements:
        db.execute(statement)
    db.reset_pool()
