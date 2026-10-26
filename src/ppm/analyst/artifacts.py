"""Artifact store: every table, chart and report is a first-class row."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from ppm import db


def _summary(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "head": json.loads(frame.head(5).to_json(orient="records", date_format="iso")),
        "dtypes": {str(column): str(dtype) for column, dtype in frame.dtypes.items()},
    }


def save_dataframe(run_id: str, task_id: str | None, name: str, path: str, frame: pd.DataFrame) -> str:
    row = db.fetch_one(
        "INSERT INTO analysis_artifact (run_id, task_id, kind, name, path, mime, rows, columns, summary) "
        "VALUES (%s, %s, 'dataframe', %s, %s, 'application/vnd.apache.parquet', %s, %s, %s) RETURNING artifact_id",
        (
            run_id, task_id, name, path, int(len(frame)),
            json.dumps([str(column) for column in frame.columns]),
            json.dumps(_summary(frame)),
        ),
    )
    return str(row["artifact_id"])


def save_chart(run_id: str, task_id: str | None, name: str, path: str) -> str:
    row = db.fetch_one(
        "INSERT INTO analysis_artifact (run_id, task_id, kind, name, path, mime, summary) "
        "VALUES (%s, %s, 'chart', %s, %s, 'image/png', %s) RETURNING artifact_id",
        (run_id, task_id, name, path, json.dumps({"bytes": Path(path).stat().st_size})),
    )
    return str(row["artifact_id"])


def save_report(run_id: str, name: str, path: str, frame: pd.DataFrame | None = None) -> str:
    row = db.fetch_one(
        "INSERT INTO analysis_artifact (run_id, task_id, kind, name, path, mime, rows, columns, summary) "
        "VALUES (%s, NULL, 'report', %s, %s, 'text/markdown', %s, %s, %s) RETURNING artifact_id",
        (
            run_id, name, path, len(frame) if frame is not None else 0,
            json.dumps([str(column) for column in frame.columns]) if frame is not None else "[]",
            json.dumps({"bytes": Path(path).stat().st_size}),
        ),
    )
    return str(row["artifact_id"])


def get_artifact(artifact_id: str) -> dict[str, Any] | None:
    return db.fetch_one("SELECT * FROM analysis_artifact WHERE artifact_id = %s", (artifact_id,))


def list_artifacts(run_id: str) -> list[dict[str, Any]]:
    return db.fetch_all(
        "SELECT * FROM analysis_artifact WHERE run_id = %s ORDER BY created_at", (run_id,)
    )
