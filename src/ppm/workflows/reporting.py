"""The report drafter graph (plan section 6, Phase 4; apps 3-4).

``gather -> draft -> sign-off interrupt -> export``.

The export node is the only place a draft becomes a file on disk, and it is
reachable only through the approver's interrupt: the sign-off gate is graph
topology, not a UI convention. Rejected drafts are recorded in report_run and
never exported. The gathered data comes only from PPM's own truth layer.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ppm import auth, db
from ppm.config import REPO_ROOT, Settings, get_settings
from ppm.models import ModelUnavailable, get_report_drafter

DEFAULT_EXPORT_DIR = REPO_ROOT / "exports" / "reports"


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60] or "project"


class ReportState(TypedDict, total=False):
    thread_id: str
    actor_id: str
    project_id: str
    gathered: dict
    draft: str
    draft_path: str
    decision: dict
    status: str
    error: str | None


def _update_run(thread_id: str, status: str, *, draft: str | None = None,
                draft_path: str | None = None, approver_id: str | None = None,
                note: str | None = None, error: str | None = None,
                create: bool = False, project_id: str | None = None,
                actor_id: str | None = None) -> None:
    if create:
        db.execute(
            "INSERT INTO report_run (thread_id, project_id, status) VALUES (%s, %s, %s) "
            "ON CONFLICT (thread_id) DO UPDATE SET status = EXCLUDED.status, updated_at = now()",
            (thread_id, project_id, status),
        )
        return
    db.execute(
        "UPDATE report_run SET status = %s, draft = COALESCE(%s, draft), "
        "draft_path = COALESCE(%s, draft_path), approver_id = COALESCE(%s, approver_id), "
        "note = COALESCE(%s, note), updated_at = now() WHERE thread_id = %s",
        (status, draft, draft_path, approver_id, note, thread_id),
    )


class ReportWorkflow:
    def __init__(self, settings: Settings | None = None, export_dir: Path | str | None = None) -> None:
        self.settings = settings or get_settings()
        self.export_dir = Path(export_dir) if export_dir else DEFAULT_EXPORT_DIR
        self._app = None

    # --- nodes -----------------------------------------------------------------

    def _fail(self, state: ReportState, message: str) -> dict:
        _update_run(state["thread_id"], "failed", error=message)
        return {"status": "failed", "error": message}

    def _gather(self, state: ReportState) -> dict:
        project = db.fetch_one("SELECT * FROM project WHERE project_id = %s", (state["project_id"],))
        if project is None:
            return self._fail(state, f"project {state['project_id']} not found")
        deliverables = db.fetch_all(
            "SELECT type, status, due_date, document_ref FROM deliverable "
            "WHERE project_id = %s ORDER BY due_date NULLS LAST", (state["project_id"],)
        )
        plan = db.fetch_one(
            "SELECT stage, plan_date, total_cost_idr, area_basis FROM cost_plan "
            "WHERE project_id = %s ORDER BY plan_date DESC LIMIT 1", (state["project_id"],)
        )
        rate_count = db.fetch_one(
            "SELECT count(*) AS n FROM benchmark_rate r JOIN cost_plan c ON c.costplan_id = r.costplan_id "
            "WHERE c.project_id = %s", (state["project_id"],)
        )
        gathered = {
            "project": {"name": project["name"], "sector": project["sector"], "stage": project["stage"],
                        "status": project["status"]},
            "deliverables": deliverables,
            "latest_cost_plan": dict(plan) if plan else None,
            "rate_count": rate_count["n"] if rate_count else 0,
        }
        json.dumps(gathered, default=str)  # fail here if not serialisable
        return {"gathered": json.loads(json.dumps(gathered, default=str)), "status": "gathered"}

    def _draft(self, state: ReportState) -> dict:
        from ppm.registry import get_registry

        meta = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M"),
            "run_id": state["thread_id"],
            "provider": self.settings.provider,
            "registry_version": get_registry(str(self.settings.registry_dir)).version,
        }
        try:
            draft = get_report_drafter(self.settings).draft(state["gathered"], meta)
        except ModelUnavailable as exc:
            return self._fail(state, str(exc))
        _update_run(state["thread_id"], "draft", draft=draft)
        return {"draft": draft, "status": "drafted"}

    def _signoff(self, state: ReportState) -> dict:
        _update_run(state["thread_id"], "awaiting_signoff")
        decision = interrupt({
            "kind": "report_signoff",
            "project": (state["gathered"].get("project") or {}).get("name"),
            "draft": state["draft"][:6000],
            "instructions": ("Resume with {'action': 'approve'|'reject', 'approver_id': <person uuid>, "
                             "'note': str} - an approver must sign; export happens only on approve."),
        })
        decision = decision or {"action": "approve"}
        approver = auth.get_person(decision.get("approver_id")) if decision.get("approver_id") else None
        if approver is None:
            return self._fail(state, "sign-off needs a valid approver_id")
        if not approver["is_approver"]:
            return self._fail(state, f"{approver['username']} is not a named approver")
        return {"decision": decision, "status": "decided"}

    def _export(self, state: ReportState) -> dict:
        decision = state.get("decision") or {}
        project_name = (state["gathered"].get("project") or {}).get("name", "project")
        self.export_dir.mkdir(parents=True, exist_ok=True)
        path = self.export_dir / f"report-{_slug(project_name)}-{time.strftime('%Y%m%d-%H%M%S')}.md"
        path.write_text(state["draft"], encoding="utf-8")
        _update_run(state["thread_id"], "approved", draft_path=str(path),
                    approver_id=decision.get("approver_id"), note=decision.get("note"))
        return {"draft_path": str(path), "status": "approved"}

    def _record_rejection(self, state: ReportState) -> dict:
        decision = state.get("decision") or {}
        _update_run(state["thread_id"], "rejected",
                    approver_id=decision.get("approver_id"), note=decision.get("note"))
        return {"status": "rejected"}

    # --- routing -------------------------------------------------------------------

    def _after_gather(self, state: ReportState) -> str:
        return "go" if state.get("status") == "gathered" else "stop"

    def _after_draft(self, state: ReportState) -> str:
        return "go" if state.get("status") == "drafted" else "stop"

    def _after_signoff(self, state: ReportState) -> str:
        if state.get("status") != "decided":
            return "stop"
        return "reject" if (state.get("decision") or {}).get("action") == "reject" else "export"

    # --- assembly --------------------------------------------------------------------

    def compile(self, checkpointer=None):
        graph = StateGraph(ReportState)
        graph.add_node("gather", self._gather)
        graph.add_node("draft", self._draft)
        graph.add_node("signoff", self._signoff)
        graph.add_node("export", self._export)
        graph.add_node("record_rejection", self._record_rejection)
        graph.add_edge(START, "gather")
        graph.add_conditional_edges("gather", self._after_gather, {"go": "draft", "stop": END})
        graph.add_conditional_edges("draft", self._after_draft, {"go": "signoff", "stop": END})
        graph.add_conditional_edges("signoff", self._after_signoff,
                                    {"export": "export", "reject": "record_rejection", "stop": END})
        graph.add_edge("export", END)
        graph.add_edge("record_rejection", END)
        self._app = graph.compile(checkpointer=checkpointer)
        return self._app

    # --- execution ---------------------------------------------------------------------

    def run(self, actor_id: str, project_id: str, thread_id: str | None = None) -> dict:
        thread_id = thread_id or str(uuid4())
        known = db.fetch_one("SELECT 1 FROM project WHERE project_id = %s", (project_id,))
        if known is None:
            return {"thread_id": thread_id, "status": "failed", "error": f"project {project_id} not found"}
        _update_run(thread_id, "draft", create=True, project_id=project_id, actor_id=actor_id)
        state: ReportState = {"thread_id": thread_id, "actor_id": actor_id, "project_id": str(project_id)}
        return self._app.invoke(state, config={"configurable": {"thread_id": thread_id}})

    def resume(self, thread_id: str, decision: dict) -> dict:
        return self._app.invoke(Command(resume=decision), config={"configurable": {"thread_id": thread_id}})
