"""Tools exposed to the analyst chat. Each is a thin, auditable wrapper."""

from __future__ import annotations

from typing import Any

from ppm.analyst.artifacts import get_artifact, list_artifacts
from ppm.analyst.catalog import catalog_text, run_sql
from ppm.analyst.codegen import OpenAICompatibleCodegen
from ppm.analyst.executor import run_plan
from ppm.analyst.planner import plan_question
from ppm.analyst.schemas import AnalysisPlan
from ppm.config import get_settings


def schema_catalog(access_level: str = "all") -> str:
    return catalog_text(access_level)


def sql_query(sql: str, limit: int = 200, access_level: str = "all") -> list[dict[str, Any]]:
    return run_sql(sql, limit=limit, access_level=access_level)


def document_search(question: str, limit: int = 5) -> dict[str, Any]:
    from ppm.workflows.rag import AskWorkflow

    return AskWorkflow().ask(question)


def plan_analysis(question: str, access_level: str = "all") -> AnalysisPlan:
    return plan_question(question, get_settings(), access_level=access_level)


def run_analysis(plan: AnalysisPlan, access_level: str = "all") -> dict[str, Any]:
    settings = get_settings()
    result = run_plan(
        plan,
        user_label=settings.user_label,
        codegen=OpenAICompatibleCodegen(settings),
        approved=True,
        model=settings.llm_model,
        access_level=access_level,
    )
    return {
        "run_id": result.run_id,
        "status": result.status,
        "report_path": result.report_path,
        "error": result.error,
        "artifacts": result.artifacts,
    }


def get_artifact_by_id(artifact_id: str) -> dict[str, Any] | None:
    return get_artifact(artifact_id)


def artifacts_for_run(run_id: str) -> list[dict[str, Any]]:
    return list_artifacts(run_id)


def teach(run_id: str, note: str) -> str:
    from ppm.analyst.teach import record_teaching

    return record_teaching(run_id, note)
