"""Change/risk registers on the compiled action compiler: creates are open,
updates carry peer/owner gates, and the report drafter picks both up."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.postgres import PostgresSaver

from ppm import auth, db
from ppm.workflows.actions import ActionWorkflow, load_actions
from ppm.workflows.ingest import interrupted
from tests.conftest import requires_db


@pytest.fixture()
def register_context(clean_db):
    people = {
        "author": auth.create_person("author", "Author", "pw", role="consultant"),
        "peer": auth.create_person("peer", "Peer", "pw", role="senior_consultant"),
        "approver": auth.create_person("approver", "Approver", "pw", role="approver", is_approver=True),
    }
    project = db.fetch_one(
        "INSERT INTO project (name, sector) VALUES ('PPM REGISTER TEST', 'industrial') RETURNING project_id"
    )
    return {"people": people, "project_id": str(project["project_id"])}


def _flow(settings, registry, action_name):
    actions = load_actions(registry, settings.app_specs_dir)
    workflow = ActionWorkflow(actions[action_name], registry, settings)
    return workflow, actions


def _run_plain(settings, registry, action_name, actor_id, params):
    workflow, _ = _flow(settings, registry, action_name)
    with PostgresSaver.from_conn_string(settings.dsn) as checkpointer:
        workflow.compile(checkpointer)
        return workflow.run(actor_id, params)


def test_register_actions_declared(settings, registry):
    actions = load_actions(registry, settings.app_specs_dir)
    assert actions["change.update"].gate == "peer"
    assert actions["risk.update"].gate == "owner"
    assert actions["change.create"].gate == "none"


@requires_db
def test_change_lifecycle_with_peer_gate(settings, registry, register_context):
    data = register_context
    result = _run_plain(settings, registry, "change.create", data["people"]["author"], {
        "project_id": data["project_id"],
        "description": "Additional retaining wall due to ground conditions",
        "cause": "ground conditions",
        "cost_impact_idr": "125000000",
        "status": "identified",
    })
    assert result["status"] == "completed", result.get("error")
    change_id = result["result"]["change_id"]

    # peer-gated update: author cannot approve their own change
    workflow, _ = _flow(settings, registry, "change.update")
    with PostgresSaver.from_conn_string(settings.dsn) as checkpointer:
        workflow.compile(checkpointer)
        result = workflow.run(data["people"]["author"], {"change_id": change_id, "status": "submitted"})
        assert interrupted(result)
        bad = workflow.resume(result["thread_id"], {
            "action": "approve", "approver_id": data["people"]["author"],
        })
        assert bad["status"] == "failed"
        assert "other than the author" in bad["error"]

    # a fresh update approved by a peer completes
    workflow, _ = _flow(settings, registry, "change.update")
    with PostgresSaver.from_conn_string(settings.dsn) as checkpointer:
        workflow.compile(checkpointer)
        result = workflow.run(data["people"]["author"], {"change_id": change_id, "status": "priced"})
        assert interrupted(result)
        result = workflow.resume(result["thread_id"], {
            "action": "approve", "approver_id": data["people"]["peer"], "note": "checked",
        })
    assert result["status"] == "completed"
    change = db.fetch_one("SELECT * FROM change_event WHERE change_id = %s", (change_id,))
    assert change["status"] == "priced"

    run = db.fetch_one("SELECT status FROM action_run WHERE action = 'change.update' "
                       "ORDER BY created_at DESC LIMIT 1")
    assert run["status"] == "completed"


@requires_db
def test_risk_update_owner_gate(settings, registry, register_context):
    data = register_context
    result = _run_plain(settings, registry, "risk.create", data["people"]["author"], {
        "project_id": data["project_id"],
        "description": "Long-lead switchgear delivery may slip",
        "category": "risk", "likelihood": "4", "impact": "3", "status": "open",
    })
    assert result["status"] == "completed", result.get("error")
    risk_id = result["result"]["risk_id"]

    workflow, _ = _flow(settings, registry, "risk.update")
    with PostgresSaver.from_conn_string(settings.dsn) as checkpointer:
        workflow.compile(checkpointer)
        result = workflow.run(data["people"]["author"], {"risk_id": risk_id, "status": "monitoring"})
        assert interrupted(result)
        bad = workflow.resume(result["thread_id"], {
            "action": "approve", "approver_id": data["people"]["peer"],
        })
        assert bad["status"] == "failed"
        assert "not an approver" in bad["error"]

    workflow, _ = _flow(settings, registry, "risk.update")
    with PostgresSaver.from_conn_string(settings.dsn) as checkpointer:
        workflow.compile(checkpointer)
        result = workflow.run(data["people"]["author"], {"risk_id": risk_id, "status": "escalated"})
        assert interrupted(result)
        result = workflow.resume(result["thread_id"], {
            "action": "approve", "approver_id": data["people"]["approver"],
        })
    assert result["status"] == "completed"
    risk = db.fetch_one("SELECT * FROM risk WHERE risk_id = %s", (risk_id,))
    assert risk["status"] == "escalated"


@requires_db
def test_report_includes_registers(settings, registry, register_context, stub_settings, tmp_path):
    from ppm.workflows.reporting import ReportWorkflow

    data = register_context
    _run_plain(settings, registry, "change.create", data["people"]["author"], {
        "project_id": data["project_id"], "description": "Extra piling at grid C", "status": "identified",
    })
    _run_plain(settings, registry, "risk.create", data["people"]["author"], {
        "project_id": data["project_id"], "description": "Monsoon season access", "category": "risk",
        "likelihood": "3", "impact": "4", "status": "open",
    })
    workflow = ReportWorkflow(stub_settings, export_dir=tmp_path)
    with PostgresSaver.from_conn_string(stub_settings.dsn) as checkpointer:
        workflow.compile(checkpointer)
        result = workflow.run(data["people"]["author"], data["project_id"])
    assert interrupted(result)
    draft = result["draft"]
    assert "## Change register" in draft and "Extra piling at grid C" in draft
    assert "## Risk register (RAID)" in draft and "Monsoon season access" in draft
