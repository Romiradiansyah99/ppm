"""Teach loop: a user correction becomes a benchmark proposal, not an auto-merge."""

from __future__ import annotations

import json
from typing import Any

from ppm import db
from ppm.analyst.artifacts import list_artifacts
from ppm.analyst.codegen import OpenAICompatibleCodegen
from ppm.config import Settings, get_settings


def record_teaching(run_id: str, note: str) -> str:
    row = db.fetch_one(
        "INSERT INTO teaching_event (run_id, user_note, status) VALUES (%s, %s, 'proposed') "
        "RETURNING event_id",
        (run_id, note),
    )
    return str(row["event_id"])


def list_teaching() -> list[dict[str, Any]]:
    return db.fetch_all(
        "SELECT event_id, run_id, user_note, proposed_lesson, status, created_at "
        "FROM teaching_event ORDER BY created_at DESC"
    )


def propose_lesson(event_id: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    event = db.fetch_one("SELECT * FROM teaching_event WHERE event_id = %s", (event_id,))
    if not event:
        raise ValueError("teaching event not found")
    run = db.fetch_one("SELECT * FROM analysis_run WHERE run_id = %s", (event["run_id"],))
    artifacts = list_artifacts(event["run_id"])
    report = None
    for artifact in artifacts:
        if artifact["kind"] == "report":
            from pathlib import Path

            path = Path(artifact["path"])
            if path.is_file():
                report = path.read_text(encoding="utf-8")[:8000]
                break

    lesson = event["user_note"]
    try:
        client = OpenAICompatibleCodegen(settings)
        prompt = (
            "You are improving PPM Analyst. Read the run and the user's correction.\n"
            "Propose a concise lesson for the planner/codegen context. Do not change data.\n"
            "Return plain text, max 120 words.\n\n"
            f"Question: {run['question'] if run else ''}\n"
            f"User note: {event['user_note']}\n"
            f"Report:\n{report or '(no report)'}\n"
        )
        lesson = client._chat([{"role": "user", "content": prompt}]).strip()
    except Exception:
        lesson = event["user_note"]

    db.execute(
        "UPDATE teaching_event SET proposed_lesson = %s, status = 'proposed' WHERE event_id = %s",
        (lesson, event_id),
    )
    return lesson


def create_benchmark(question: str, checks: dict, source_run_id: str | None = None) -> str:
    row = db.fetch_one(
        "INSERT INTO benchmark_case (question, expected_checks, source_run_id, status) "
        "VALUES (%s, %s, %s, 'active') RETURNING case_id",
        (question, json.dumps(checks), source_run_id),
    )
    return str(row["case_id"])


def list_benchmarks() -> list[dict[str, Any]]:
    return db.fetch_all(
        "SELECT case_id, question, expected_checks, status, active, created_at "
        "FROM benchmark_case ORDER BY created_at DESC"
    )
