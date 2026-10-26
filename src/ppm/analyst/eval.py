"""Benchmark evaluation for the analyst."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

from ppm import db
from ppm.analyst.artifacts import list_artifacts
from ppm.analyst.codegen import codegen_for
from ppm.analyst.executor import run_plan
from ppm.analyst.planner import plan_question
from ppm.config import Settings, get_settings


def load_cases(path: str | Path) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    cases = data.get("cases") or []
    if not isinstance(cases, list):
        raise ValueError("benchmark file must contain a 'cases' list")
    return cases


def _check_case(case: dict, run_status: str, artifacts: list[dict], report: str | None) -> tuple[bool, list[str]]:
    checks = case.get("expected") or {}
    problems: list[str] = []
    if run_status != "completed":
        problems.append(f"run status is {run_status}")

    frames = []
    for artifact in artifacts:
        if artifact["kind"] == "dataframe":
            try:
                frames.append(pd.read_parquet(artifact["path"]))
            except Exception:
                continue

    columns_any = checks.get("columns_any") or []
    if columns_any:
        hit = any(any(column in frame.columns for column in columns_any) for frame in frames)
        if not hit:
            problems.append(f"none of the expected columns appeared: {columns_any}")

    min_rows = int(checks.get("min_rows", 0))
    if min_rows and not any(len(frame) >= min_rows for frame in frames):
        problems.append(f"no result has >= {min_rows} rows")

    contains_any = checks.get("contains_any") or []
    if contains_any:
        haystack = ""
        for frame in frames:
            haystack += frame.astype(str).to_csv(index=False)
        haystack += report or ""
        if not any(token.lower() in haystack.lower() for token in contains_any):
            problems.append(f"none of the expected values appeared: {contains_any}")

    report_contains = checks.get("report_contains") or []
    if report_contains:
        for token in report_contains:
            if token.lower() not in (report or "").lower():
                problems.append(f"report does not contain: {token}")

    return not problems, problems


def evaluate(path: str | Path, settings: Settings | None = None, codegen_kind: str = "api") -> dict:
    settings = settings or get_settings()
    cases = load_cases(path)
    items: list[dict] = []

    for case in cases:
        question = str(case.get("question", "")).strip()
        if not question:
            continue
        item = {"id": case.get("id"), "question": question}
        try:
            plan = plan_question(question, settings, access_level="all")
            result = run_plan(
                plan,
                user_label=settings.user_label,
                codegen=codegen_for(settings, codegen_kind),
                approved=True,
                model=settings.llm_model or codegen_kind,
                access_level="all",
            )
            artifacts = list_artifacts(result.run_id)
            report = None
            for artifact in artifacts:
                if artifact["kind"] == "report" and artifact["path"]:
                    path_obj = Path(artifact["path"])
                    if path_obj.is_file():
                        report = path_obj.read_text(encoding="utf-8")
                        break
            passed, problems = _check_case(case, result.status, artifacts, report)
            item.update({
                "status": result.status,
                "run_id": result.run_id,
                "passed": passed,
                "problems": problems,
                "error": result.error,
            })
        except Exception as exc:
            item.update({"status": "error", "passed": False, "problems": [str(exc)]})
        items.append(item)

    total = len(items)
    passed = sum(1 for item in items if item["passed"])
    summary = {
        "total": total,
        "passed": passed,
        "score": round(passed / total, 4) if total else 0.0,
        "generated_at": datetime.utcnow().isoformat(),
        "model": settings.llm_model,
    }
    return {"summary": summary, "items": items}


def write_report(report: dict, directory: str | Path) -> Path:
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = target / f"analyst-eval-{stamp}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path
