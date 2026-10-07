"""Tracker: deliverables and stage-gate tracking, plus report drafting with
a mandatory sign-off (Phase 4 apps)."""

import sys
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
st.set_page_config(page_title="PPM Tracker", layout="wide")

from ppm import db                                                           # noqa: E402
from ui_common import (                                                      # noqa: E402
    dispatch_action,
    draft_report,
    get_context,
    require_user,
    resolve_report,
    user_badge,
)

person = require_user()
user_badge()
settings, registry, actions = get_context()

st.title("Tracker")
st.caption("Deliverables, stage gates and the report drafter - export happens only after sign-off.")

projects = db.fetch_all("SELECT project_id, name FROM project ORDER BY name")
project_names = {str(row["project_id"]): row["name"] for row in projects}

tab_deliverables, tab_reports = st.tabs(["Deliverables", "Reports"])

# --- deliverables -----------------------------------------------------------------
with tab_deliverables:
    col_a, col_b = st.columns(2)
    project_filter = col_a.selectbox("Project", ["(all)"] + list(project_names.values()), key="del_project")
    status_filter = col_b.selectbox(
        "Status", ["(all)"] + registry.enum_values("deliverable", "status"), key="del_status"
    )

    query = (
        "SELECT d.*, p.name AS project_name, per.display_name AS reviewer_name "
        "FROM deliverable d JOIN project p ON p.project_id = d.project_id "
        "LEFT JOIN person per ON per.person_id = d.reviewer_id WHERE TRUE"
    )
    params: list = []
    if project_filter != "(all)":
        query += " AND p.name = %s"
        params.append(project_filter)
    if status_filter != "(all)":
        query += " AND d.status = %s"
        params.append(status_filter)
    query += " ORDER BY d.due_date NULLS LAST, d.type"
    rows = db.fetch_all(query, params)
    if rows:
        st.dataframe(
            [
                {
                    "Deliverable": row["type"],
                    "Project": row["project_name"],
                    "Status": row["status"],
                    "Due": row["due_date"].isoformat() if row["due_date"] else "no date",
                    "Reviewer": row["reviewer_name"] or "",
                    "Ref": row["document_ref"] or "",
                }
                for row in rows
            ],
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("No deliverables match. Add one below.")

    with st.expander("Add deliverable"):
        with st.form("deliverable_create", clear_on_submit=True):
            project = st.selectbox("Project", list(project_names.values()))
            dtype = st.selectbox("Type", registry.enum_values("deliverable", "type"))
            due = st.date_input("Due date", value=date.today())
            ref = st.text_input("Document ref", placeholder="optional")
            created = st.form_submit_button("Create", type="primary")
        if created and project_names:
            project_id = next(pid for pid, name in project_names.items() if name == project)
            result = dispatch_action(actions, settings, registry, "deliverable.create", person["person_id"], {
                "project_id": project_id, "type": dtype, "due_date": due.isoformat(),
                "status": "open", "document_ref": ref or None,
            })
            if result.get("status") == "completed":
                st.success("Deliverable created.")
                st.rerun()
            else:
                st.error(result.get("error") or result.get("status"))

    if rows:
        with st.expander("Update status"):
            labels = {
                f"{row['type']} - {row['project_name']} ({row['status']})": str(row["deliverable_id"])
                for row in rows
            }
            choice = st.selectbox("Deliverable", list(labels))
            new_status = st.selectbox("New status", registry.enum_values("deliverable", "status"))
            new_due = st.date_input("Due date", value=date.today())
            if st.button("Update"):
                result = dispatch_action(actions, settings, registry, "deliverable.update", person["person_id"], {
                    "deliverable_id": labels[choice], "status": new_status, "due_date": new_due.isoformat(),
                })
                if result.get("status") == "completed":
                    st.success("Updated.")
                    st.rerun()
                else:
                    st.error(result.get("error") or result.get("status"))

# --- reports -------------------------------------------------------------------------
with tab_reports:
    st.subheader("Draft a monthly report")
    if project_names:
        project = st.selectbox("Project", list(project_names.values()), key="report_project")
        if st.button("Draft report (goes to sign-off)"):
            project_id = next(pid for pid, name in project_names.items() if name == project)
            result = draft_report(settings, project_id, person["person_id"])
            if result.get("__interrupt__"):
                st.info(f"Draft ready - awaiting a named approver's sign-off (run {result['thread_id'][:8]}...).")
            elif result.get("status") == "approved":
                st.success(f"Already signed off: {result.get('draft_path')}")
            else:
                st.error(result.get("error") or result.get("status"))
    else:
        st.info("No projects yet - ingest a cost plan first.")

    pending = db.fetch_all(
        "SELECT r.*, p.name AS project_name FROM report_run r LEFT JOIN project p ON p.project_id = r.project_id "
        "WHERE r.status = 'awaiting_signoff' ORDER BY r.created_at"
    )
    st.subheader(f"Awaiting sign-off ({len(pending)})")
    for run in pending:
        with st.expander(f"{run['project_name'] or '?'} - {run['created_at']:%Y-%m-%d %H:%M}"):
            st.markdown(run["draft"] or "(no draft text)")
            if person["is_approver"]:
                note = st.text_input("Note", key=f"rnote_{run['run_id']}")
                col_a, col_b = st.columns(2)
                if col_a.button("Approve & export", key=f"rappr_{run['run_id']}", type="primary"):
                    result = resolve_report(settings, run["thread_id"], {
                        "action": "approve", "approver_id": person["person_id"], "note": note,
                    })
                    if result.get("status") == "approved":
                        st.success(f"Exported: {result.get('draft_path')}")
                        st.rerun()
                    else:
                        st.error(result.get("error") or result.get("status"))
                if col_b.button("Reject", key=f"rrej_{run['run_id']}"):
                    result = resolve_report(settings, run["thread_id"], {
                        "action": "reject", "approver_id": person["person_id"], "note": note,
                    })
                    if result.get("status") == "rejected":
                        st.warning("Rejected - nothing exported.")
                        st.rerun()
                    else:
                        st.error(result.get("error") or result.get("status"))
            else:
                st.caption("Only a named approver can sign this off.")

    decided = db.fetch_all(
        "SELECT r.*, p.name AS project_name FROM report_run r LEFT JOIN project p ON p.project_id = r.project_id "
        "WHERE r.status IN ('approved', 'rejected', 'failed') ORDER BY r.updated_at DESC LIMIT 20"
    )
    if decided:
        st.subheader("History")
        st.dataframe(
            [
                {
                    "Project": row["project_name"] or "?",
                    "Status": row["status"],
                    "Export": row["draft_path"] or "",
                    "Note": row["note"] or "",
                    "Updated": row["updated_at"].strftime("%Y-%m-%d %H:%M"),
                }
                for row in decided
            ],
            use_container_width=True, hide_index=True,
        )
