"""Report drafter E2E: the sign-off gate is graph topology - export only
exists past an approver's interrupt."""

from __future__ import annotations

import uuid

import pytest
from langgraph.checkpoint.postgres import PostgresSaver

from ppm import auth, db
from ppm.workflows.ingest import interrupted
from ppm.workflows.reporting import ReportWorkflow
from tests.conftest import requires_db


@pytest.fixture()
def project_with_deliverables(clean_db):
    people = {
        "author": auth.create_person("author", "Author", "pw", role="consultant"),
        "approver": auth.create_person("approver", "Approver", "pw", role="approver", is_approver=True),
        "other": auth.create_person("other", "Other", "pw", role="consultant"),
    }
    project = db.fetch_one(
        "INSERT INTO project (name, sector, stage) VALUES ('PPM REPORT TEST', 'commercial', 'construction') "
        "RETURNING project_id"
    )
    project_id = str(project["project_id"])
    db.execute(
        "INSERT INTO deliverable (project_id, type, status, due_date) VALUES (%s, 'monthly_report', 'in_progress', '2026-10-31')",
        (project_id,),
    )
    db.execute(
        "INSERT INTO deliverable (project_id, type, status, due_date) VALUES (%s, 'cashflow', 'open', NULL)",
        (project_id,),
    )
    return {"people": people, "project_id": project_id}


def _flow(settings, registry, export_dir):
    workflow = ReportWorkflow(settings, export_dir=export_dir)
    return workflow


@requires_db
def test_draft_pauses_for_signoff(settings, registry, project_with_deliverables, stub_settings, tmp_path):
    data = project_with_deliverables
    workflow = _flow(stub_settings, registry, tmp_path)
    with PostgresSaver.from_conn_string(stub_settings.dsn) as checkpointer:
        workflow.compile(checkpointer)
        result = workflow.run(data["people"]["author"], data["project_id"])

        assert interrupted(result)
        assert "PPM REPORT TEST" in result["draft"]
        assert "monthly_report" in result["draft"]
        assert "cashflow" in result["draft"]

        run = db.fetch_one("SELECT * FROM report_run WHERE thread_id = %s", (result["thread_id"],))
        assert run["status"] == "awaiting_signoff"
        assert run["draft"] and "PPM REPORT TEST" in run["draft"]
        assert run["draft_path"] is None
        assert not list(tmp_path.iterdir())          # nothing exported while unsigned


@requires_db
def test_approval_exports_the_draft(settings, registry, project_with_deliverables, stub_settings, tmp_path):
    data = project_with_deliverables
    workflow = _flow(stub_settings, registry, tmp_path)
    with PostgresSaver.from_conn_string(stub_settings.dsn) as checkpointer:
        workflow.compile(checkpointer)
        result = workflow.run(data["people"]["author"], data["project_id"])
        assert interrupted(result)
        result = workflow.resume(result["thread_id"], {
            "action": "approve", "approver_id": data["people"]["approver"], "note": "ok to issue",
        })
    assert result["status"] == "approved"

    exported = list(tmp_path.iterdir())
    assert len(exported) == 1
    content = exported[0].read_text(encoding="utf-8")
    assert "PPM REPORT TEST" in content

    run = db.fetch_one("SELECT * FROM report_run")
    assert run["status"] == "approved"
    assert run["draft_path"] == str(exported[0])
    assert str(run["approver_id"]) == data["people"]["approver"]
    assert run["note"] == "ok to issue"


@requires_db
def test_rejection_never_exports(settings, registry, project_with_deliverables, stub_settings, tmp_path):
    data = project_with_deliverables
    workflow = _flow(stub_settings, registry, tmp_path)
    with PostgresSaver.from_conn_string(stub_settings.dsn) as checkpointer:
        workflow.compile(checkpointer)
        result = workflow.run(data["people"]["author"], data["project_id"])
        assert interrupted(result)
        result = workflow.resume(result["thread_id"], {
            "action": "reject", "approver_id": data["people"]["approver"], "note": "numbers wrong",
        })
    assert result["status"] == "rejected"
    assert not list(tmp_path.iterdir())
    run = db.fetch_one("SELECT * FROM report_run")
    assert run["status"] == "rejected"
    assert run["draft_path"] is None


@requires_db
def test_non_approver_cannot_sign_off(settings, registry, project_with_deliverables, stub_settings, tmp_path):
    data = project_with_deliverables
    workflow = _flow(stub_settings, registry, tmp_path)
    with PostgresSaver.from_conn_string(stub_settings.dsn) as checkpointer:
        workflow.compile(checkpointer)
        result = workflow.run(data["people"]["author"], data["project_id"])
        assert interrupted(result)
        result = workflow.resume(result["thread_id"], {
            "action": "approve", "approver_id": data["people"]["other"],
        })
    assert result["status"] == "failed"
    assert "not a named approver" in result["error"]
    assert not list(tmp_path.iterdir())


@requires_db
def test_unknown_project_fails_cleanly(settings, registry, project_with_deliverables, stub_settings, tmp_path):
    workflow = _flow(stub_settings, registry, tmp_path)
    with PostgresSaver.from_conn_string(stub_settings.dsn) as checkpointer:
        workflow.compile(checkpointer)
        result = workflow.run(project_with_deliverables["people"]["author"], str(uuid.uuid4()))
    assert result["status"] == "failed"
    assert "not found" in result["error"]
