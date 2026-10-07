"""The ingest graph (plan section 6, step 3).

load -> register Document (hash) -> extract elements (structured output) ->
validate against the definition registry -> [human review interrupt if
low-confidence or failing rows] -> normalise -> write BenchmarkRate rows with
document_id + source_ref.

This is the only path that writes entity rows (plan section 3: no direct DB
writes from any app). The review gate is a LangGraph interrupt with a
Postgres checkpointer, so a paused document survives restarts and shows up in
the approver's queue (plan section 4).
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ppm import db
from ppm.config import Settings, get_settings
from ppm.loaders import ParsedDocument, load_document
from ppm.models import ModelUnavailable, embed_texts, get_extractor
from ppm.normalise import normalise_elements
from ppm.registry import Registry, get_registry
from ppm.schemas import CostPlanExtraction, NormalisedRate, ValidationIssue, ValidationReport


class IngestState(TypedDict, total=False):
    thread_id: str
    source_path: str
    registry_version: str
    document_id: str | None
    parsed: dict | None
    extraction: dict | None
    validation: dict | None
    normalised: list[dict] | None
    review: dict | None
    status: str
    error: str | None
    stats: dict


def _update_run(thread_id: str, status: str, *, document_id: str | None = None,
                stats: dict | None = None, error: str | None = None,
                create: bool = False, source_path: str | None = None) -> None:
    if create:
        db.execute(
            "INSERT INTO ingest_run (thread_id, document_id, source_path, status, stats, error) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (thread_id) DO UPDATE SET "
            "status = EXCLUDED.status, stats = EXCLUDED.stats, error = EXCLUDED.error, updated_at = now()",
            (thread_id, document_id, source_path, status, json.dumps(stats or {}), error),
        )
        return
    db.execute(
        "UPDATE ingest_run SET status = %s, stats = COALESCE(%s, stats), error = %s, "
        "document_id = COALESCE(%s, document_id), updated_at = now() WHERE thread_id = %s",
        (status, json.dumps(stats) if stats is not None else None, error, document_id, thread_id),
    )


class IngestWorkflow:
    def __init__(self, settings: Settings | None = None, registry: Registry | None = None) -> None:
        self.settings = settings or get_settings()
        self.registry = registry or get_registry(str(self.settings.registry_dir))
        self._app = None

    # --- nodes -----------------------------------------------------------------

    def _load_register(self, state: IngestState) -> dict:
        try:
            parsed = load_document(state["source_path"])
        except (FileNotFoundError, ValueError) as exc:
            return self._fail(state, str(exc))

        _update_run(state["thread_id"], "running", create=True, source_path=state["source_path"])

        existing = db.fetch_one("SELECT document_id, ingest_status FROM document WHERE hash = %s", (parsed.hash,))
        if existing and existing["ingest_status"] == "indexed":
            _update_run(state["thread_id"], "duplicate", document_id=str(existing["document_id"]))
            return {
                "status": "duplicate",
                "document_id": str(existing["document_id"]),
                "stats": {"duplicates": 1},
                "error": None,
            }

        row = db.fetch_one(
            "INSERT INTO document (hash, source_path, mime, ingest_status, licence_class) "
            "VALUES (%s, %s, %s, 'reference_only', 'permitted') "
            "ON CONFLICT (hash) DO UPDATE SET source_path = EXCLUDED.source_path "
            "RETURNING document_id",
            (parsed.hash, parsed.source_path, parsed.mime),
        )
        document_id = str(row["document_id"])
        _update_run(state["thread_id"], "running", document_id=document_id)
        return {
            "document_id": document_id,
            "parsed": parsed.to_state(),
            "status": "running",
            "error": None,
            "stats": {"source_path": parsed.source_path, "hash": parsed.hash},
        }

    def _extract(self, state: IngestState) -> dict:
        parsed = ParsedDocument.from_state(state["parsed"])
        try:
            extractor = get_extractor(self.settings)
            extraction = extractor.extract(parsed, self.registry)
        except ModelUnavailable as exc:
            return self._fail(state, str(exc))
        except Exception as exc:
            return self._fail(state, f"extraction failed: {exc}")
        return {"extraction": extraction.model_dump(mode="json")}

    def _validate(self, state: IngestState) -> dict:
        extraction = CostPlanExtraction.model_validate(state["extraction"])
        rows, issues, dropped = normalise_elements(
            extraction,
            self.registry,
            review_threshold=self.settings.review_threshold,
            blocklist=self.registry.redaction_blocklist(),
        )

        if not extraction.project_name.strip():
            issues.append(ValidationIssue(severity="error", field="project_name",
                                          message="no project name - cannot resolve the project record"))
        if extraction.stage not in self.registry.enum_values("cost_plan", "stage"):
            issues.append(ValidationIssue(severity="warning", field="stage",
                                          message=f"stage '{extraction.stage}' not in registry enum"))
        if extraction.area_basis and extraction.area_basis not in self.registry.area_bases():
            issues.append(ValidationIssue(severity="warning", field="area_basis",
                                          message=f"area basis '{extraction.area_basis}' not in registry"))
        if extraction.rate_basis and extraction.rate_basis not in self.registry.rate_bases():
            issues.append(ValidationIssue(severity="warning", field="rate_basis",
                                          message=f"rate basis '{extraction.rate_basis}' not in registry"))
        if not rows:
            issues.append(ValidationIssue(severity="error", field="elements",
                                          message="no normalisable rate rows in this document"))

        review_items = self._review_items(extraction, issues)
        review_required = bool([i for i in issues if i.severity == "error"]) or any(
            i.field == "confidence" for i in issues
        )
        report = ValidationReport(issues=issues, review_required=review_required, review_items=review_items)

        stats = dict(state.get("stats") or {})
        stats.update({
            "elements_extracted": len(extraction.elements),
            "rows_valid": len(rows),
            "rows_dropped": dropped,
            "issues": [i.model_dump(mode="json") for i in issues],
        })
        return {"validation": report.model_dump(mode="json"), "stats": stats}

    def _review_items(self, extraction: CostPlanExtraction, issues: list[ValidationIssue]) -> list[dict]:
        """Only what the reviewer must decide on: hard errors and
        low-confidence rows. Definition-registry warnings (e.g. unresolved
        element codes) are surfaced by stats/`ppm status`, not the queue."""
        items: list[dict] = []
        for issue in issues:
            if issue.severity != "error" and issue.field != "confidence":
                continue
            if issue.row_index is None:
                items.append({"row_index": None, "source_ref": None, "description": None,
                              "severity": issue.severity, "problem": f"{issue.field}: {issue.message}"})
                continue
            element = extraction.elements[issue.row_index] if issue.row_index < len(extraction.elements) else None
            items.append({
                "row_index": issue.row_index,
                "source_ref": element.source_ref if element else None,
                "description": element.description if element else None,
                "confidence": element.confidence if element else None,
                "severity": issue.severity,
                "problem": f"{issue.field}: {issue.message}",
            })
        return items

    def _human_review(self, state: IngestState) -> dict:
        report = ValidationReport.model_validate(state["validation"])
        decisions = interrupt({
            "kind": "ingest_review",
            "document_id": state.get("document_id"),
            "source_path": state["source_path"],
            "items": report.review_items,
            "instructions": (
                "Resume with {'action': 'approve'|'reject', 'drop_rows': [element indexes], 'note': str}. "
                "approve writes every valid row (minus drop_rows); reject marks the document reference_only."
            ),
        })
        decisions = decisions or {"action": "approve"}
        if decisions.get("action") == "reject":
            return {"review": decisions, "status": "reviewed"}

        drop = {int(i) for i in decisions.get("drop_rows", [])}
        extraction = CostPlanExtraction.model_validate(state["extraction"])
        if drop:
            kept = [e for i, e in enumerate(extraction.elements) if i not in drop]
            extraction = extraction.model_copy(update={"elements": kept})
        stats = dict(state.get("stats") or {})
        stats["rows_dropped"] = stats.get("rows_dropped", 0) + len(drop)
        _update_run(state["thread_id"], "running", stats=stats)
        return {"review": decisions, "extraction": extraction.model_dump(mode="json"), "stats": stats}

    def _normalise(self, state: IngestState) -> dict:
        extraction = CostPlanExtraction.model_validate(state["extraction"])
        rows, issues, dropped = normalise_elements(
            extraction,
            self.registry,
            review_threshold=self.settings.review_threshold,
            blocklist=self.registry.redaction_blocklist(),
        )
        stats = dict(state.get("stats") or {})
        stats["rows_dropped"] = stats.get("rows_dropped", 0) + dropped
        return {
            "normalised": [row.model_dump(mode="json") for row in rows],
            "stats": stats,
        }

    def _persist(self, state: IngestState) -> dict:
        extraction = CostPlanExtraction.model_validate(state["extraction"])
        rows = [NormalisedRate.model_validate(r) for r in state.get("normalised") or []]
        parsed_records = state.get("parsed") or {}
        source_path = state["source_path"]
        document_hash = str(parsed_records.get("hash") or "")
        stats = dict(state.get("stats") or {})

        if extraction.plan_date is None or not rows:
            return self._fail(state, "nothing writable after review (missing plan date or zero rows)")

        embeddings: list[list[float] | None] = [None] * len(rows)
        embedding_warning = None
        try:
            vectors = embed_texts(self.settings, [r.description for r in rows])
            embeddings = list(vectors)
        except ModelUnavailable as exc:
            embedding_warning = f"embeddings skipped: {exc}"

        try:
            with db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO project (name, sector, stage, confidentiality_class) VALUES (%s, %s, %s, 'internal') "
                    "ON CONFLICT (name) DO UPDATE SET sector = COALESCE(EXCLUDED.sector, project.sector), "
                    "stage = COALESCE(EXCLUDED.stage, project.stage), updated_at = now() "
                    "RETURNING project_id",
                    (extraction.project_name.strip(), extraction.sector, extraction.stage),
                )
                project_id = str(cur.fetchone()[0])

                cur.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM cost_plan WHERE project_id = %s", (project_id,))
                version = int(cur.fetchone()[0])

                total_cost = extraction.total_cost_idr
                if total_cost is None:
                    amounts = [e.amount_idr for e in extraction.elements if e.amount_idr]
                    total_cost = sum(amounts) if amounts else None

                cur.execute(
                    "INSERT INTO cost_plan (project_id, version, stage, plan_date, currency, gfa_m2, area_basis, "
                    "total_cost_idr, rate_basis, elements, basis_notes, author_id, document_id, document_ref, "
                    "registry_version, extraction_confidence) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING costplan_id",
                    (
                        project_id, version, extraction.stage, extraction.plan_date, extraction.currency,
                        extraction.gfa_m2, extraction.area_basis,
                        round(total_cost) if total_cost is not None else None,
                        extraction.rate_basis,
                        json.dumps([e.model_dump(mode="json") for e in extraction.elements]),
                        extraction.basis_notes, extraction.author,
                        state["document_id"], f"{document_hash}:{source_path}",
                        self.registry.version, extraction.confidence,
                    ),
                )
                costplan_id = str(cur.fetchone()[0])

                for row, vector in zip(rows, embeddings):
                    cur.execute(
                        "INSERT INTO benchmark_rate (costplan_id, document_id, element_code, description, rate_idr, "
                        "unit, date_normalised_to, location, sector, spec_level, client_identifiable, source_ref, embedding) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (costplan_id, element_code, description, source_ref) DO NOTHING",
                        (
                            costplan_id, state["document_id"], row.element_code, row.description, row.rate_idr,
                            row.unit, row.date_normalised_to,
                            row.location or self.settings.location_default,
                            row.sector, row.spec_level, row.client_identifiable, row.source_ref, vector,
                        ),
                    )

                cur.execute(
                    "UPDATE document SET ingest_status = 'indexed', project_id = %s, doc_date = %s, author = %s "
                    "WHERE document_id = %s",
                    (project_id, extraction.plan_date, extraction.author, state["document_id"]),
                )
        except Exception as exc:
            return self._fail(state, f"write failed: {exc}")

        stats.update({
            "project_id": project_id, "costplan_id": costplan_id,
            "rates_written": len(rows), "embedding_warning": embedding_warning,
        })
        stats["rows_dropped"] = max(0, stats.get("elements_extracted", 0) - len(rows))
        _update_run(state["thread_id"], "completed", document_id=state["document_id"], stats=stats)
        return {"status": "completed", "stats": stats, "error": None}

    def _reject(self, state: IngestState) -> dict:
        db.execute("UPDATE document SET ingest_status = 'reference_only' WHERE document_id = %s",
                   (state.get("document_id"),))
        stats = dict(state.get("stats") or {})
        stats["review"] = state.get("review")
        _update_run(state["thread_id"], "rejected", stats=stats)
        return {"status": "rejected", "stats": stats}

    def _fail(self, state: IngestState, message: str) -> dict:
        _update_run(state["thread_id"], "failed", error=message)
        return {"status": "failed", "error": message}

    # --- routing ------------------------------------------------------------------

    def _after_load(self, state: IngestState) -> str:
        return "continue" if state.get("status") == "running" else "stop"

    def _after_validate(self, state: IngestState) -> str:
        report = ValidationReport.model_validate(state["validation"])
        return "review" if report.review_required else "normalise"

    def _after_review(self, state: IngestState) -> str:
        action = (state.get("review") or {}).get("action", "approve")
        return "reject" if action == "reject" else "normalise"

    # --- assembly ------------------------------------------------------------------

    def compile(self, checkpointer=None):
        graph = StateGraph(IngestState)
        graph.add_node("load_register", self._load_register)
        graph.add_node("extract", self._extract)
        graph.add_node("validate", self._validate)
        graph.add_node("human_review", self._human_review)
        graph.add_node("normalise", self._normalise)
        graph.add_node("persist", self._persist)
        graph.add_node("reject", self._reject)

        graph.add_edge(START, "load_register")
        graph.add_conditional_edges("load_register", self._after_load, {"continue": "extract", "stop": END})
        graph.add_edge("extract", "validate")
        graph.add_conditional_edges("validate", self._after_validate, {"review": "human_review", "normalise": "normalise"})
        graph.add_conditional_edges("human_review", self._after_review, {"reject": "reject", "normalise": "normalise"})
        graph.add_edge("normalise", "persist")
        graph.add_edge("persist", END)
        graph.add_edge("reject", END)

        self._app = graph.compile(checkpointer=checkpointer)
        return self._app

    # --- execution -------------------------------------------------------------------

    def run(self, source_path: str, thread_id: str | None = None) -> dict:
        thread_id = thread_id or str(uuid4())
        _update_run(thread_id, "running", create=True, source_path=str(source_path))
        state: IngestState = {
            "thread_id": thread_id,
            "source_path": str(source_path),
            "registry_version": self.registry.version,
            "status": "running",
            "stats": {},
        }
        return self._app.invoke(state, config={"configurable": {"thread_id": thread_id}})

    def resume(self, thread_id: str, decisions: dict) -> dict:
        from langgraph.types import Command

        return self._app.invoke(Command(resume=decisions), config={"configurable": {"thread_id": thread_id}})


def interrupted(result: dict) -> bool:
    return bool(result.get("__interrupt__"))
