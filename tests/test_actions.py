"""Compiled action graphs E2E: validation, gates, approval roundtrips, trust
weighting, and the action_run ledger."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from langgraph.checkpoint.postgres import PostgresSaver

from ppm import auth, db
from ppm.workflows.actions import ActionWorkflow, load_actions
from ppm.workflows.ingest import interrupted
from tests.conftest import requires_db


@pytest.fixture()
def people(clean_db):
    return {
        "author": auth.create_person("author", "Author Person", "pw", role="consultant"),
        "approver": auth.create_person("approver", "Approver Person", "pw", role="approver", is_approver=True),
        "other": auth.create_person("other", "Other Person", "pw", role="consultant"),
    }


@contextmanager
def _flow(settings, registry, action_name):
    actions = load_actions(registry, settings.app_specs_dir)
    workflow = ActionWorkflow(actions[action_name], registry, settings)
    with PostgresSaver.from_conn_string(settings.dsn) as checkpointer:
        workflow.compile(checkpointer)
        yield workflow


def _submit_lesson(settings, registry, actor_id, text="Check crane lead times early.") -> str:
    with _flow(settings, registry, "lesson.submit") as workflow:
        result = workflow.run(actor_id, {"text_content": text, "sector": "commercial"})
    assert result["status"] == "completed", result.get("error")
    return result["result"]["lesson_id"]


def test_load_actions_from_specs(settings, registry):
    actions = load_actions(registry, settings.app_specs_dir)
    assert {"lesson.submit", "lesson.promote", "lesson.remove"} <= set(actions)
    promote = actions["lesson.promote"]
    assert promote.gate == "owner"
    assert promote.operation == "update"
    assert promote.key_field == "lesson_id"


@requires_db
def test_submit_creates_private_lesson(settings, registry, people):
    with _flow(settings, registry, "lesson.submit") as workflow:
        result = workflow.run(people["author"], {"text_content": "Watch tower crane rates.", "sector": "commercial"})
    assert result["status"] == "completed"

    lesson = db.fetch_one("SELECT * FROM lesson_learned")
    assert lesson["layer"] == "private"
    assert lesson["status"] == "draft"
    assert str(lesson["author_id"]) == people["author"]
    assert 0.0 < float(lesson["trust_weight"]) <= 1.0

    run = db.fetch_one("SELECT * FROM action_run")
    assert run["status"] == "completed"
    assert run["action"] == "lesson.submit"


@requires_db
def test_submit_rejects_unknown_and_missing_fields(settings, registry, people):
    with _flow(settings, registry, "lesson.submit") as workflow:
        bad = workflow.run(people["author"], {"text_content": "x", "trust_weight": "0.99", "sneaky": "1"})
        assert bad["status"] == "failed"
        assert "not writable" in bad["error"]
        empty = workflow.run(people["author"], {})
        assert empty["status"] == "failed"
        assert "missing required" in empty["error"]
    assert db.fetch_one("SELECT count(*) AS n FROM lesson_learned")["n"] == 0


@requires_db
def test_gated_action_rejects_non_approver_then_approves(settings, registry, people):
    lesson_one = _submit_lesson(settings, registry, people["author"])

    # the submitter is not an approver: the pause must survive, and an
    # unauthorised resume must fail without writing
    with _flow(settings, registry, "lesson.promote") as workflow:
        result = workflow.run(people["author"], {"lesson_id": lesson_one})
        assert interrupted(result)
        run = db.fetch_one("SELECT status FROM action_run WHERE thread_id = %s", (result["thread_id"],))
        assert run["status"] == "running"          # CLI/UI flips this to awaiting_approval
        bad = workflow.resume(result["thread_id"], {"action": "approve", "approver_id": people["other"]})
        assert bad["status"] == "failed"
        assert "approver" in bad["error"]
    lesson = db.fetch_one("SELECT * FROM lesson_learned WHERE lesson_id = %s", (lesson_one,))
    assert lesson["layer"] == "private"            # nothing leaked to the shared layer

    # a fresh promotion approved by the approver completes and shares the lesson
    lesson_two = _submit_lesson(settings, registry, people["author"], "Second lesson.")
    with _flow(settings, registry, "lesson.promote") as workflow:
        result = workflow.run(people["author"], {"lesson_id": lesson_two})
        assert interrupted(result)
        result = workflow.resume(result["thread_id"], {"action": "approve", "approver_id": people["approver"]})
    assert result["status"] == "completed", result.get("error")
    lesson = db.fetch_one("SELECT * FROM lesson_learned WHERE lesson_id = %s", (lesson_two,))
    assert lesson["layer"] == "shared" and lesson["status"] == "shared"
    assert str(lesson["approver_id"]) == people["approver"]

    logs = db.fetch_all("SELECT * FROM lesson_weight_log WHERE lesson_id = %s ORDER BY log_id", (lesson_two,))
    assert len(logs) == 1                           # weight *changes* are logged (old -> new)
    assert logs[0]["reason"] == "promotion accepted"
    assert float(logs[0]["old_weight"]) == 0.25     # set at submit, unchanged since
    assert float(logs[0]["new_weight"]) > float(logs[0]["old_weight"])


@requires_db
def test_rejection_decays_weight(settings, registry, people):
    lesson_one = _submit_lesson(settings, registry, people["author"], "Lesson to be rejected.")
    weight_at_submit = float(db.fetch_one(
        "SELECT trust_weight FROM lesson_learned WHERE lesson_id = %s", (lesson_one,)
    )["trust_weight"])

    with _flow(settings, registry, "lesson.promote") as workflow:
        result = workflow.run(people["author"], {"lesson_id": lesson_one})
        assert interrupted(result)
        result = workflow.resume(result["thread_id"], {
            "action": "reject", "approver_id": people["approver"], "note": "not reusable",
        })
    assert result["status"] == "rejected"
    rejected = db.fetch_one("SELECT * FROM lesson_learned WHERE lesson_id = %s", (lesson_one,))
    assert rejected["status"] == "rejected"
    assert float(rejected["trust_weight"]) == 0.0

    # the next submission from the same author carries a decayed weight
    lesson_two = _submit_lesson(settings, registry, people["author"], "Next lesson after rejection.")
    weight_after = float(db.fetch_one(
        "SELECT trust_weight FROM lesson_learned WHERE lesson_id = %s", (lesson_two,)
    )["trust_weight"])
    assert weight_after < weight_at_submit


@requires_db
def test_remove_lesson_requires_owner_gate(settings, registry, people):
    lesson_id = _submit_lesson(settings, registry, people["author"])
    with _flow(settings, registry, "lesson.remove") as workflow:
        result = workflow.run(people["author"], {"lesson_id": lesson_id})
        assert interrupted(result)
        result = workflow.resume(result["thread_id"], {"action": "approve", "approver_id": people["approver"]})
    assert result["status"] == "completed"
    lesson = db.fetch_one("SELECT * FROM lesson_learned WHERE lesson_id = %s", (lesson_id,))
    assert lesson["status"] == "removed"


@requires_db
def test_promote_unknown_lesson_fails(settings, registry, people):
    with _flow(settings, registry, "lesson.promote") as workflow:
        result = workflow.run(people["author"], {"lesson_id": "00000000-0000-0000-0000-000000000000"})
    assert result["status"] == "failed"
    assert "not found" in result["error"]
