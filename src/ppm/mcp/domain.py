"""Plain-Python PPM tools exposed through MCP.

Every tool verifies the signed context token and the role of this MCP
instance. No LangChain decorators or agent loop live here.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ppm import db
from ppm.analyst.artifacts import list_artifacts
from ppm.analyst.catalog import catalog_text, run_sql
from ppm.analyst.codegen import OpenAICompatibleCodegen
from ppm.analyst.documents import create_from_template
from ppm.analyst.executor import run_plan
from ppm.analyst.memory import remember, set_preference
from ppm.analyst.planner import plan_question
from ppm.config import Settings, get_settings
from ppm.tool_context import ContextError, verify
from ppm.workflows.rag import AskWorkflow
from ppm.workflows.retrieval import TemplateQuery, find_templates


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _settings() -> Settings:
    return get_settings()


def _access_level() -> str:
    value = os.environ.get("PPM_ACCESS_LEVEL", "baseline").strip().lower()
    return "all" if value == "all" else "baseline"


def _context(token: str) -> dict[str, Any]:
    secret = os.environ.get("PPM_TOOL_SECRET", "").strip()
    if not secret:
        raise ContextError("PPM_TOOL_SECRET is not configured for the MCP server")
    return verify(token, secret, required_access=_access_level())


def schema_catalog(context: str) -> str:
    """List the analyst views, columns and row counts visible to this PPM role."""
    try:
        ctx = _context(context)
        return catalog_text(ctx["access_level"])
    except Exception as exc:
        return _json({"error": str(exc)})


def sql_query(context: str, sql: str) -> str:
    """Run a read-only SELECT/WITH query over the analyst_* views."""
    try:
        ctx = _context(context)
        return _json(run_sql(sql, limit=200, access_level=ctx["access_level"]))
    except Exception as exc:
        return _json({"error": str(exc)})


def document_search(context: str, question: str) -> str:
    """Search the cited template/procedure corpus and return sources."""
    try:
        ctx = _context(context)
        result = AskWorkflow(_settings()).ask(question, access_level=ctx["access_level"])
        return _json({
            "answer": result.get("answer"),
            "sources": [
                {
                    "source_path": chunk.get("source_path"),
                    "section_ref": chunk.get("section_ref"),
                }
                for chunk in (result.get("chunks") or [])[:6]
            ],
            "degraded": result.get("degraded"),
        })
    except Exception as exc:
        return _json({"error": str(exc)})


def plan_and_run(context: str, question: str) -> str:
    """Plan and execute a multi-step project-cost/risk/change analysis."""
    try:
        ctx = _context(context)
        settings = _settings()
        settings.require_api()
        plan = plan_question(question, settings, access_level=ctx["access_level"])
        result = run_plan(
            plan,
            user_label=ctx["display_name"],
            codegen=OpenAICompatibleCodegen(settings),
            approved=True,
            model=settings.llm_model,
            access_level=ctx["access_level"],
            user_id=ctx["user_id"],
            conversation_id=ctx["conversation_id"],
        )
        artifacts = [
            {"kind": item["kind"], "name": item["name"], "artifact_id": str(item["artifact_id"])}
            for item in list_artifacts(result.run_id)
        ]
        return _json({
            "run_id": result.run_id,
            "status": result.status,
            "error": result.error,
            "plan": {
                "assumptions": plan.assumptions,
                "tasks": [{"id": task.id, "name": task.name, "kind": task.kind} for task in plan.tasks],
            },
            "artifacts": artifacts,
        })
    except Exception as exc:
        return _json({"error": str(exc)})


def find_templates(context: str, query: str) -> str:
    """Find office templates by code or title before drafting a document."""
    try:
        _context(context)
        rows = find_templates(TemplateQuery(text=query, limit=8), log=False)
        return _json([
            {
                "code": row.get("code"),
                "title": row.get("title"),
                "stage": row.get("stage"),
                "category": row.get("category"),
                "format": row.get("format"),
            }
            for row in rows
        ])
    except Exception as exc:
        return _json({"error": str(exc)})


def create_document(
    context: str,
    template: str,
    project: str = "",
    fields_json: str = "{}",
    auto_pick: bool = False,
) -> str:
    """Create a DRAFT document owned by the current user and conversation."""
    try:
        ctx = _context(context)
        fields = json.loads(fields_json or "{}")
        if not isinstance(fields, dict):
            raise ValueError("fields_json must be an object")
        user = {
            "user_id": ctx["user_id"],
            "username": ctx["username"],
            "display_name": ctx["display_name"],
            "role": ctx["role"],
            "access_level": ctx["access_level"],
        }
        result = create_from_template(
            template,
            user=user,
            project_name=project or None,
            fields={str(key): str(value) for key, value in fields.items()},
            auto_pick=auto_pick,
            conversation_id=ctx["conversation_id"],
        )
        result.pop("path", None)
        return _json(result)
    except Exception as exc:
        return _json({"error": str(exc)})


def remember_preference(context: str, content: str, kind: str = "preference") -> str:
    """Save a durable preference or fact for the current user only."""
    try:
        ctx = _context(context)
        memory_id = remember(
            ctx["user_id"],
            content,
            kind=kind if kind in {"preference", "fact", "project_context", "correction"} else "preference",
            conversation_id=ctx["conversation_id"],
            source="agent",
        )
        if kind == "preference":
            set_preference(ctx["user_id"], "note." + str(memory_id)[:8], content, source="agent")
        return _json({"saved": True, "memory_id": memory_id})
    except Exception as exc:
        return _json({"saved": False, "error": str(exc)})


def corpus_stats(context: str) -> str:
    """Return corpus document, chunk, template and ingest-run counts."""
    try:
        _context(context)
        document_rows = db.fetch_all(
            "SELECT ingest_status, count(*) AS count FROM document GROUP BY ingest_status ORDER BY ingest_status"
        )
        chunk_count = db.fetch_one("SELECT count(*) AS count FROM document_chunk")["count"]
        template_count = db.fetch_one("SELECT count(*) AS count FROM template")["count"]
        run_rows = db.fetch_all(
            "SELECT status, count(*) AS count FROM ingest_run GROUP BY status ORDER BY status"
        )
        return _json({
            "documents": {row["ingest_status"]: row["count"] for row in document_rows},
            "chunks": chunk_count,
            "templates": template_count,
            "ingest_runs": {row["status"]: row["count"] for row in run_rows},
        })
    except Exception as exc:
        return _json({"error": str(exc)})


def run_cost_demo(context: str, question: str) -> str:
    """Run the deterministic cost-benchmark demo (admin instance only)."""
    try:
        ctx = _context(context)
        if ctx["access_level"] != "all":
            return _json({"error": "admin access required"})
        from ppm.analyst.demo import demo_codegen, demo_plan

        result = run_plan(
            demo_plan(),
            user_label=ctx["display_name"],
            codegen=demo_codegen(),
            approved=True,
            model="static-demo",
            access_level="all",
            user_id=ctx["user_id"],
            conversation_id=ctx["conversation_id"],
        )
        artifacts = [
            {"kind": item["kind"], "name": item["name"], "artifact_id": str(item["artifact_id"])}
            for item in list_artifacts(result.run_id)
        ]
        return _json({"run_id": result.run_id, "status": result.status, "artifacts": artifacts})
    except Exception as exc:
        return _json({"error": str(exc)})
