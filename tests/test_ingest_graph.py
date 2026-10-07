"""End-to-end ingest graph tests against the real database with the
deterministic stub provider: the plan's Phase 0 verification checklist
(every rate carries document_id + source_ref; review queue works; duplicates
are detected; hybrid search returns provenance)."""

from __future__ import annotations

from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver

from ppm import db
from ppm.loaders import load_document
from ppm.models import _StubExtractor
from ppm.workflows.ingest import IngestWorkflow, interrupted
from ppm.workflows.retrieval import RateQuery, rate_search
from tests.conftest import requires_db


@contextmanager
def _run(samples, settings, registry, name):
    """Compiled workflow with a live checkpointer - valid inside the block."""
    workflow = IngestWorkflow(settings, registry)
    with PostgresSaver.from_conn_string(settings.dsn) as checkpointer:
        workflow.compile(checkpointer)
        yield workflow, workflow.run(str(samples[name]))


def _review_payload(result: dict) -> dict:
    return (result.get("__interrupt__") or ())[0].value


# --- stub extractor (no database) -------------------------------------------

def test_stub_extraction_on_alpha(samples, registry):
    parsed = load_document(samples["alpha"])
    extraction = _StubExtractor().extract(parsed, registry)
    assert extraction.project_name == "PPM SYNTHETIC ALPHA"
    assert extraction.stage == "elemental"
    assert extraction.plan_date.isoformat() == "2024-03-15"
    assert extraction.area_basis == "GFA"
    assert extraction.rate_basis == "incl_prelims"
    assert extraction.sector == "commercial"
    assert len(extraction.elements) == 14
    first = extraction.elements[0]
    assert first.code == "A10"
    assert first.rate_idr == 45000
    assert first.unit == "m2"
    assert first.source_ref == "Cost Plan!R13"


def test_stub_extraction_gamma_flags_missing_unit(samples, registry):
    parsed = load_document(samples["gamma"])
    extraction = _StubExtractor().extract(parsed, registry)
    assert len(extraction.elements) == 12
    assert extraction.sector is None                      # no sector line in gamma
    assert extraction.elements[-1].unit is None           # deliberate defect row


# --- full graph against postgres --------------------------------------------

@requires_db
def test_ingest_alpha_writes_rates_with_provenance(samples, registry, stub_settings, clean_db):
    with _run(samples, stub_settings, registry, "alpha") as (workflow, result):
        assert result["status"] == "completed"
        assert result["stats"]["rates_written"] == 14

    rows = db.fetch_all("SELECT * FROM benchmark_rate ORDER BY source_ref")
    assert len(rows) == 14
    assert all(row["document_id"] is not None for row in rows)
    assert all(row["source_ref"].startswith("Cost Plan!R") for row in rows)
    assert rows[0]["source_ref"] == "Cost Plan!R13"
    assert rows[0]["rate_idr"] == 45000
    assert rows[0]["unit"] == "m2"
    assert str(rows[0]["date_normalised_to"]) == "2024-03-15"
    assert all(row["embedding"] is not None for row in rows)

    document = db.fetch_one("SELECT * FROM document")
    assert document["ingest_status"] == "indexed"
    plan = db.fetch_one("SELECT * FROM cost_plan")
    assert plan["version"] == 1
    assert plan["area_basis"] == "GFA"
    assert plan["total_cost_idr"] and plan["total_cost_idr"] > 0
    assert plan["document_ref"].startswith(document["hash"])
    project = db.fetch_one("SELECT * FROM project")
    assert project["name"] == "PPM SYNTHETIC ALPHA"
    assert project["sector"] == "commercial"

    run = db.fetch_one("SELECT status FROM ingest_run WHERE thread_id = %s", (result["thread_id"],))
    assert run["status"] == "completed"

    chunks = db.fetch_all("SELECT * FROM document_chunk ORDER BY chunk_index")
    assert chunks and all(chunk["section_ref"] for chunk in chunks)
    assert all(chunk["embedding"] is not None for chunk in chunks)
    assert result["stats"]["chunks_written"] == len(chunks)


@requires_db
def test_duplicate_file_is_detected(samples, registry, stub_settings, clean_db):
    with _run(samples, stub_settings, registry, "alpha") as (_, first):
        assert first["status"] == "completed"
    with _run(samples, stub_settings, registry, "alpha") as (_, second):
        assert second["status"] == "duplicate"
    count = db.fetch_one("SELECT count(*) AS n FROM benchmark_rate")
    assert count["n"] == 14


@requires_db
def test_gamma_review_roundtrip_approve_with_drop(samples, registry, stub_settings, clean_db):
    with _run(samples, stub_settings, registry, "gamma") as (workflow, result):
        assert interrupted(result)
        items = _review_payload(result)["items"]
        assert any(item["severity"] == "error" for item in items)
        result = workflow.resume(result["thread_id"], {"action": "approve", "drop_rows": [10]})

    assert result["status"] == "completed"
    assert result["stats"]["rates_written"] == 10          # 12 - defect row - dropped row

    refs = {row["source_ref"] for row in db.fetch_all("SELECT source_ref FROM benchmark_rate")}
    assert "Cost Plan!R22" not in refs                     # dropped in review
    assert "Cost Plan!R23" not in refs                     # defect row (missing unit)
    descriptions = [row["description"] for row in db.fetch_all("SELECT description FROM benchmark_rate")]
    assert any("[redacted]" in d for d in descriptions)
    assert not any("PT SYNTHETIC CLIENT" in d for d in descriptions)

    # gamma has no sector line: everything renders flagged unclassified (failsafe)
    assert all(row["sector"] is None for row in db.fetch_all("SELECT * FROM benchmark_rate"))


@requires_db
def test_gamma_review_reject(samples, registry, stub_settings, clean_db):
    with _run(samples, stub_settings, registry, "gamma") as (workflow, result):
        assert interrupted(result)
        result = workflow.resume(result["thread_id"], {"action": "reject", "note": "test"})
    assert result["status"] == "rejected"
    assert db.fetch_one("SELECT count(*) AS n FROM benchmark_rate")["n"] == 0
    assert db.fetch_one("SELECT ingest_status FROM document")["ingest_status"] == "reference_only"


@requires_db
def test_hybrid_search_returns_provenance_and_logs(samples, registry, stub_settings, clean_db):
    with _run(samples, stub_settings, registry, "alpha"):
        pass
    with _run(samples, stub_settings, registry, "beta"):
        pass

    rows, degraded = rate_search(RateQuery(text="reinforcement bar", limit=10), stub_settings)
    assert degraded is None
    assert rows
    assert all(row["similarity"] is not None for row in rows)
    assert all(row["document_hash"] and row["source_ref"] for row in rows)

    commercial, _ = rate_search(RateQuery(sector="commercial"), stub_settings)
    assert commercial and all(row["sector"] == "commercial" for row in commercial)

    classified = rate_search(RateQuery(sector="infrastructure"), stub_settings)[0]
    assert classified and all(not row["unclassified"] for row in classified)

    logged = db.fetch_one("SELECT count(*) AS n FROM search_log WHERE user_label = %s", (stub_settings.user_label,))
    assert logged["n"] == 3


@requires_db
def test_second_upload_gets_next_cost_plan_version(samples, registry, stub_settings, clean_db, tmp_path):
    from openpyxl import load_workbook

    with _run(samples, stub_settings, registry, "alpha") as (_, first):
        assert first["status"] == "completed"

    edited = tmp_path / "alpha_v2.xlsx"
    workbook = load_workbook(samples["alpha"])
    sheet = workbook["Cost Plan"]
    sheet.append([99, "Z99", "Additional provisional sum item", "sum", 1, "500.000.000", "500.000.000", "low"])
    workbook.save(edited)

    with _run({"v2": edited}, stub_settings, registry, "v2") as (_, second):
        assert second["status"] == "completed"
    versions = db.fetch_all("SELECT version FROM cost_plan ORDER BY version")
    assert [v["version"] for v in versions] == [1, 2]
