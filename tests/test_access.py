import pytest

from ppm.analyst.catalog import run_sql
from ppm.data.seed import seed_defaults

pytestmark = pytest.mark.db


def test_junior_sees_only_baseline(clean_db):
    seed_defaults()

    baseline_projects = run_sql(
        "SELECT project_id, name, data_class FROM analyst_project", access_level="baseline"
    )
    all_projects = run_sql(
        "SELECT project_id, name, data_class FROM analyst_project", access_level="all"
    )

    assert len(baseline_projects) == 7
    assert len(all_projects) == 12
    assert all(row["data_class"] == "baseline" for row in baseline_projects)
    assert any(row["data_class"] == "restricted" for row in all_projects)
    assert not any(row["name"].startswith("Benchmark B") for row in baseline_projects)
    assert any(row["name"].startswith("Benchmark B") for row in all_projects)


def test_junior_line_items_are_filtered(clean_db):
    seed_defaults()
    baseline = run_sql(
        "SELECT costplan_id, line_id FROM analyst_cost_line", access_level="baseline"
    )
    everything = run_sql(
        "SELECT costplan_id, line_id FROM analyst_cost_line", access_level="all"
    )
    assert len(baseline) == 21
    assert len(everything) == 37


def test_raw_tables_are_rejected(clean_db):
    seed_defaults()
    with pytest.raises(ValueError, match="not allowed"):
        run_sql("SELECT * FROM project", access_level="all")
    with pytest.raises(ValueError, match="not allowed"):
        run_sql("SELECT * FROM analysis_run", access_level="all")


def test_cte_is_allowed(clean_db):
    seed_defaults()
    rows = run_sql(
        "WITH p AS (SELECT * FROM analyst_project) SELECT count(*) AS count FROM p",
        access_level="baseline",
    )
    assert rows[0]["count"] == 7


def test_multiple_ctes_are_allowed(clean_db):
    seed_defaults()
    rows = run_sql(
        "WITH a AS (SELECT * FROM analyst_project), "
        "b AS (SELECT * FROM a) "
        "SELECT count(*) AS count FROM b",
        access_level="baseline",
    )
    assert rows[0]["count"] == 7


def test_rag_filters_client_identifiable_documents(clean_db):
    from ppm import db
    from ppm.workflows.retrieval import ChunkQuery, chunk_search

    seed_defaults()
    text = "confidential project budget data " * 20
    documents = {}
    for name, classification in (
        ("internal-doc.txt", "internal"),
        ("client-doc.txt", "client_identifiable"),
    ):
        row = db.fetch_one(
            "INSERT INTO document (hash, source_path, canonical_path, source_collection, mime, "
            "licence_class, confidentiality_class, ingest_status) "
            "VALUES (%s, %s, %s, 'test', 'text/plain', 'office_authored', %s, 'indexed') "
            "RETURNING document_id",
            (name, name, name, classification),
        )
        documents[name] = str(row["document_id"])
        db.execute(
            "INSERT INTO document_chunk (document_id, chunk_index, section_ref, text_content, "
            "token_estimate) VALUES (%s, 0, 'page 1', %s, 40)",
            (documents[name], text),
        )

    baseline, _ = chunk_search(
        ChunkQuery(text="confidential project", limit=10),
        mode="literal", log=False, access_level="baseline",
    )
    everything, _ = chunk_search(
        ChunkQuery(text="confidential project", limit=10),
        mode="literal", log=False, access_level="all",
    )

    assert len(baseline) == 1
    assert baseline[0]["document_id"] == documents["internal-doc.txt"]
    assert len(everything) == 2
    assert {row["confidentiality_class"] for row in everything} == {
        "internal", "client_identifiable"
    }
