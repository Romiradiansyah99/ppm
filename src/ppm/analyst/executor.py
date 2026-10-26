"""Execute an approved analysis plan: datasets -> DAG tasks -> artifacts -> report."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ppm import db
from ppm.analyst.artifacts import get_artifact, save_chart, save_dataframe, save_report
from ppm.analyst.catalog import run_sql
from ppm.analyst.runtime import (
    cache_key,
    code_hash,
    execute_task,
    prepare_run_dir,
    write_inputs,
)
from ppm.analyst.schemas import AnalysisPlan, TaskSpec, plan_layers, validate_plan
from ppm.analyst.static_analysis import CodeRejected, check_code
from ppm.analyst.validators import validate_output
from ppm.config import get_settings


class RunFailure(Exception):
    pass


@dataclass
class RunResult:
    run_id: str
    status: str
    report_path: str | None = None
    error: str | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _insert_run(
    plan: AnalysisPlan,
    user_label: str,
    model: str | None,
    access_level: str = "baseline",
    user_id: str | None = None,
    conversation_id: str | None = None,
) -> str:
    row = db.fetch_one(
        "INSERT INTO analysis_run (user_label, question, plan_json, status, model, assumptions, "
        "started_at, access_level, user_id, conversation_id) "
        "VALUES (%s, %s, %s, 'running', %s, %s, now(), %s, %s, %s) RETURNING run_id",
        (
            user_label,
            plan.question,
            json.dumps(plan.model_dump(by_alias=True)),
            model,
            json.dumps(plan.assumptions),
            access_level,
            user_id,
            conversation_id,
        ),
    )
    return str(row["run_id"])


def _update_run(run_id: str, status: str, *, error: str | None = None,
                total_ms: int | None = None, plan_json: str | None = None) -> None:
    db.execute(
        "UPDATE analysis_run SET status = %s, error = %s, total_ms = %s, "
        "finished_at = CASE WHEN %s IN ('completed', 'failed', 'rejected') THEN now() ELSE finished_at END, "
        "plan_json = COALESCE(%s, plan_json) WHERE run_id = %s",
        (status, error, total_ms, status, plan_json, run_id),
    )


def _insert_task(run_id: str, task: TaskSpec, code: str, cache: str) -> str:
    row = db.fetch_one(
        "INSERT INTO analysis_task (run_id, task_key, name, kind, depends_on, code, code_hash, "
        "cache_key, status, output_schema) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'queued', %s) "
        "ON CONFLICT (run_id, task_key) DO UPDATE SET code = EXCLUDED.code, "
        "code_hash = EXCLUDED.code_hash, cache_key = EXCLUDED.cache_key, "
        "output_schema = EXCLUDED.output_schema RETURNING task_id",
        (
            run_id, task.id, task.name, task.kind, task.depends_on, code,
            code_hash(code), cache, json.dumps(task.output_schema),
        ),
    )
    return str(row["task_id"])


def _update_task(task_id: str, status: str, *, output_path: str | None = None,
                 output_hash: str | None = None, validation: dict | None = None,
                 error: str | None = None, duration_ms: int | None = None) -> None:
    db.execute(
        "UPDATE analysis_task SET status = %s, output_path = %s, output_hash = %s, "
        "validation = COALESCE(%s, validation), error = %s, duration_ms = %s, "
        "started_at = COALESCE(started_at, now()), finished_at = now() WHERE task_id = %s",
        (
            status, output_path, output_hash,
            json.dumps(validation) if validation is not None else None,
            error, duration_ms, task_id,
        ),
    )


def _cache_lookup(key: str) -> dict[str, Any] | None:
    row = db.fetch_one(
        "SELECT output_path, output_hash FROM analysis_task "
        "WHERE cache_key = %s AND status IN ('validated', 'cached') AND output_path IS NOT NULL "
        "ORDER BY created_at DESC LIMIT 1",
        (key,),
    )
    if row and row["output_path"] and Path(row["output_path"]).exists():
        return row
    return None


def _frame(rows: list[dict], schema: dict[str, str]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty and schema:
        frame = pd.DataFrame(columns=list(schema))
    return frame


def _inspect(name: str, frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "rows": int(len(frame)),
        "columns": [str(column) for column in frame.columns],
        "nulls": {str(column): int(frame[column].isna().sum()) for column in frame.columns},
    }


def _markdown_table(frame: pd.DataFrame, columns: list[str], limit: int = 10) -> str:
    columns = [column for column in columns if column in frame.columns]
    if not columns:
        return "_(no tabular columns)_"
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join(["---"] * len(columns)) + "|"]
    for _, row in frame[columns].head(limit).iterrows():
        values = []
        for column in columns:
            value = row[column]
            if isinstance(value, float):
                text = f"{value:,.2f}" if abs(value) < 1000 else f"{value:,.0f}"
            else:
                text = str(value)
            values.append(text.replace("|", "\\|"))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _build_report(
    plan: AnalysisPlan,
    inspection: dict[str, dict],
    task_results: dict[str, dict],
    outputs: dict[str, Any],
    run_dir: Path,
) -> tuple[str, pd.DataFrame | None]:
    lines = [f"# {plan.question}", ""]
    if plan.assumptions:
        lines.append("## Assumptions")
        lines.extend(f"- {item}" for item in plan.assumptions)
        lines.append("")

    lines.append("## Data inspected")
    for name, info in inspection.items():
        non_null = {key: value for key, value in info["nulls"].items() if value}
        lines.append(
            f"- **{name}**: {info['rows']} rows, {len(info['columns'])} columns"
            + (f"; nulls: {non_null}" if non_null else "")
        )
    lines.append("")

    lines.append("## Tasks")
    for key, result in task_results.items():
        validations = result.get("validation") or []
        failed = [item for item in validations if not item["passed"]]
        state = result["status"]
        if failed:
            state += f" ({len(failed)} validation failure)"
        lines.append(f"- `{key}` - {result['task'].name}: **{state}**")
    lines.append("")

    main_frame = None
    for result in reversed(list(task_results.values())):
        candidate = outputs.get(result["task"].id)
        if isinstance(candidate, pd.DataFrame) and not candidate.empty:
            main_frame = candidate
            break

    lines.append("## Result")
    if main_frame is not None:
        preferred = [
            column for column in (
                "project", "description", "line_total", "rate", "currency",
                "total_cost", "score", "cost_impact",
            ) if column in main_frame.columns
        ]
        lines.append(_markdown_table(main_frame, preferred or list(main_frame.columns)))
    else:
        lines.append("_(no tabular result)_")
    lines.append("")

    charts = [
        result for result in task_results.values()
        if result["task"].kind == "chart" and result.get("output_path")
    ]
    if charts:
        lines.append("## Charts")
        for result in charts:
            lines.append(f"- {result['output_path']}")
        lines.append("")

    lines.append("## Provenance")
    lines.append(
        "Every number above was produced by an artifact in this run. "
        "Dataset SQL, task code, input hashes and validations are stored in "
        "`analysis_run`, `analysis_task` and `analysis_artifact`."
    )
    lines.append("")

    report_path = run_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return str(report_path), main_frame


def run_plan(
    plan: AnalysisPlan,
    *,
    user_label: str,
    codegen,
    approved: bool = True,
    model: str | None = None,
    access_level: str = "baseline",
    user_id: str | None = None,
    conversation_id: str | None = None,
) -> RunResult:
    if not approved:
        raise RunFailure("plan has not been approved")
    plan = validate_plan(plan)
    settings = get_settings()

    run_id = _insert_run(
        plan, user_label, model,
        access_level=access_level, user_id=user_id, conversation_id=conversation_id,
    )
    run_dir = prepare_run_dir(run_id)
    started = time.perf_counter()
    artifacts: list[dict[str, Any]] = []
    outputs: dict[str, Any] = {}

    try:
        frames: dict[str, pd.DataFrame] = {}
        inspection: dict[str, dict] = {}
        for dataset in plan.datasets:
            if dataset.source == "sql":
                rows = run_sql(dataset.sql or "", limit=20000, access_level=access_level)
                frame = _frame(rows, dataset.schema_)
            else:
                artifact = get_artifact(dataset.artifact_id or "")
                if not artifact:
                    raise RunFailure(f"artifact not found: {dataset.artifact_id}")
                frame = pd.read_parquet(artifact["path"])
            frames[dataset.name] = frame
            inspection[dataset.name] = _inspect(dataset.name, frame)

        plan_with_inspection = plan.model_dump(by_alias=True)
        plan_with_inspection["_inspection"] = inspection
        _update_run(run_id, "running", plan_json=json.dumps(plan_with_inspection))

        available = write_inputs(run_dir, frames)
        task_results: dict[str, dict] = {}

        for layer in plan_layers(plan.tasks):
            input_schemas = {
                name: list(info.get("columns") or [])
                for name, info in available.items()
            }
            samples = {
                name: frames[name].head(3).to_dict("records")
                for name in available
                if name in frames
            }

            def _generate(task: TaskSpec) -> str:
                return task.code or codegen.generate(task, input_schemas, samples)

            with ThreadPoolExecutor(max_workers=settings.analyst_max_parallel) as pool:
                futures = {task.id: pool.submit(_generate, task) for task in layer}

            for task in layer:
                code = futures[task.id].result()
                code_error: Exception | None = None
                for attempt in range(3):
                    try:
                        check_code(code)
                        code_error = None
                        break
                    except CodeRejected as exc:
                        code_error = exc
                        repair = getattr(codegen, "repair", None)
                        if attempt == 2 or repair is None:
                            break
                        try:
                            code = repair(task, code, str(exc), input_schemas, samples)
                        except Exception as repair_exc:
                            code_error = repair_exc
                            break

                if code_error is not None:
                    task_id = _insert_task(run_id, task, code, cache_key(code, available))
                    _update_task(task_id, "failed", error=str(code_error))
                    raise RunFailure(f"task {task.id}: {code_error}")

                key = cache_key(code, available)
                task_id = _insert_task(run_id, task, code, key)
                cached = _cache_lookup(key)

                if cached:
                    output_path = cached["output_path"]
                    output_hash = cached["output_hash"]
                    status = "cached"
                    duration_ms = 0
                else:
                    if not settings.analyst_allow_code_execution:
                        raise RunFailure("code execution is disabled")
                    result = None
                    execute_error: Exception | str | None = None
                    for attempt in range(3):
                        result = execute_task(
                            run_dir, task.id, code, available,
                            timeout=settings.analyst_task_timeout,
                        )
                        if result.status == "ok":
                            break
                        execute_error = result.error or "task failed"
                        repair = getattr(codegen, "repair", None)
                        if attempt == 2 or repair is None:
                            break
                        try:
                            code = repair(
                                task, code,
                                "Runtime error:\n" + str(execute_error),
                                input_schemas, samples,
                            )
                            check_code(code)
                        except Exception as repair_exc:
                            execute_error = repair_exc
                            break

                    if result is None or result.status != "ok":
                        key = cache_key(code, available)
                        task_id = _insert_task(run_id, task, code, key)
                        _update_task(
                            task_id, "failed",
                            error=str(execute_error),
                            duration_ms=result.duration_ms if result else None,
                        )
                        raise RunFailure(f"task {task.id} failed: {execute_error}")

                    key = cache_key(code, available)
                    task_id = _insert_task(run_id, task, code, key)
                    output_path = result.output_path
                    output_hash = result.output_hash
                    status = "validated"
                    duration_ms = result.duration_ms

                if task.kind == "chart":
                    if not output_path or not Path(output_path).is_file() or Path(output_path).stat().st_size == 0:
                        _update_task(task_id, "failed", error="chart file is missing or empty")
                        raise RunFailure(f"task {task.id} produced no chart")
                    artifact_id = save_chart(run_id, task_id, f"{task.id}_{task.name}", output_path)
                    artifacts.append({"artifact_id": artifact_id, "kind": "chart", "path": output_path})
                    task_results[task.id] = {
                        "task": task, "status": status, "output_path": output_path,
                        "validation": [],
                    }
                    _update_task(task_id, status, output_path=output_path, output_hash=output_hash,
                                 validation={"checks": []}, duration_ms=duration_ms)
                    continue

                frame = pd.read_parquet(output_path)
                checks = validate_output(frame, task)
                validation = {
                    "checks": [
                        {"check": check.check, "passed": check.passed, "message": check.message}
                        for check in checks
                    ]
                }
                if any(not check.passed for check in checks):
                    _update_task(task_id, "failed", output_path=output_path,
                                 output_hash=output_hash, validation=validation,
                                 duration_ms=duration_ms)
                    failed = [check.message for check in checks if not check.passed]
                    raise RunFailure(f"task {task.id} validation failed: {failed}")

                artifact_id = save_dataframe(
                    run_id, task_id, f"{task.id}_{task.name}", output_path, frame
                )
                artifacts.append({"artifact_id": artifact_id, "kind": "dataframe", "path": output_path})
                outputs[task.id] = frame
                frames[task.id] = frame
                available[task.id] = {
                    "path": output_path,
                    "hash": output_hash or "",
                    "rows": int(len(frame)),
                    "columns": [str(column) for column in frame.columns],
                }
                task_results[task.id] = {
                    "task": task, "status": status, "output_path": output_path,
                    "validation": validation["checks"],
                }
                _update_task(task_id, status, output_path=output_path, output_hash=output_hash,
                             validation=validation, duration_ms=duration_ms)

        report_path, main_frame = _build_report(plan, inspection, task_results, outputs, run_dir)
        report_artifact = save_report(run_id, "analysis_report", report_path, main_frame)
        artifacts.append({"artifact_id": report_artifact, "kind": "report", "path": report_path})
        total_ms = int((time.perf_counter() - started) * 1000)
        _update_run(run_id, "completed", total_ms=total_ms)
        return RunResult(run_id=run_id, status="completed", report_path=report_path, artifacts=artifacts)

    except Exception as exc:
        total_ms = int((time.perf_counter() - started) * 1000)
        _update_run(run_id, "failed", error=str(exc), total_ms=total_ms)
        return RunResult(run_id=run_id, status="failed", error=str(exc), artifacts=artifacts)
