"""Change and risk registers (Phase 5). Creates are open; updates carry
peer/owner approval gates and land in the approvals queue."""

import sys
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
st.set_page_config(page_title="PPM Registers", layout="wide")

from ppm import db                                                           # noqa: E402
from ui_common import (                                                      # noqa: E402
    dispatch_action,
    get_context,
    require_user,
    user_badge,
)

person = require_user()
user_badge()
settings, registry, actions = get_context()

st.title("Registers")
st.caption("Variations and RAID entries. Updates with gates go to the approvals queue on the Knowledge page.")

projects = db.fetch_all("SELECT project_id, name FROM project ORDER BY name")
project_names = {str(row["project_id"]): row["name"] for row in projects}

tab_changes, tab_risks = st.tabs(["Change register", "Risk register (RAID)"])

# --- changes -----------------------------------------------------------------------
with tab_changes:
    changes = db.fetch_all(
        "SELECT c.*, p.name AS project_name FROM change_event c JOIN project p ON p.project_id = c.project_id "
        "ORDER BY c.created_at DESC LIMIT 200"
    )
    if changes:
        st.dataframe(
            [
                {
                    "Change": row["description"][:80],
                    "Project": row["project_name"],
                    "Status": row["status"],
                    "Cost impact": f"IDR {row['cost_impact_idr']:,}" if row["cost_impact_idr"] else "-",
                    "Days": row["time_impact_days"] or "-",
                    "Identified": row["identified_on"].isoformat() if row["identified_on"] else "-",
                }
                for row in changes
            ],
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("No changes recorded.")

    with st.expander("Record a change"):
        with st.form("change_create", clear_on_submit=True):
            project = st.selectbox("Project", list(project_names.values()), key="ch_project")
            description = st.text_area("Description")
            cause = st.text_input("Cause", placeholder="design development / instruction / ...")
            cost = st.number_input("Cost impact (IDR)", min_value=0, step=1_000_000, value=0)
            days = st.number_input("Time impact (days)", min_value=0, step=1, value=0)
            ref = st.text_input("Instruction ref")
            created = st.form_submit_button("Record", type="primary")
        if created and project_names:
            if not description.strip():
                st.warning("Description is required.")
            else:
                project_id = next(pid for pid, name in project_names.items() if name == project)
                result = dispatch_action(actions, settings, registry, "change.create", person["person_id"], {
                    "project_id": project_id, "description": description.strip(), "cause": cause or None,
                    "cost_impact_idr": str(int(cost)) if cost else None,
                    "time_impact_days": str(int(days)) if days else None,
                    "status": "identified", "instruction_ref": ref or None, "identified_on": date.today().isoformat(),
                })
                if result.get("status") == "completed":
                    st.success("Change recorded.")
                    st.rerun()
                else:
                    st.error(result.get("error") or result.get("status"))

    if changes:
        with st.expander("Update a change (peer-gated)"):
            labels = {f"{row['description'][:60]} ({row['status']})": str(row["change_id"]) for row in changes}
            choice = st.selectbox("Change", list(labels), key="ch_update")
            new_status = st.selectbox("New status", registry.enum_values("change_event", "status"))
            approved_on = st.date_input("Approved on", value=date.today())
            if st.button("Submit update"):
                result = dispatch_action(actions, settings, registry, "change.update", person["person_id"], {
                    "change_id": labels[choice], "status": new_status,
                    "approved_on": approved_on.isoformat() if new_status == "approved" else None,
                })
                if result.get("__interrupt__"):
                    st.info("Update sent for peer approval (see Knowledge page queue).")
                elif result.get("status") == "completed":
                    st.success("Updated.")
                    st.rerun()
                else:
                    st.error(result.get("error") or result.get("status"))

# --- risks ---------------------------------------------------------------------------
with tab_risks:
    risks = db.fetch_all(
        "SELECT r.*, p.name AS project_name FROM risk r JOIN project p ON p.project_id = r.project_id "
        "ORDER BY r.review_date NULLS LAST LIMIT 200"
    )
    if risks:
        st.dataframe(
            [
                {
                    "Category": row["category"],
                    "Description": row["description"][:80],
                    "Project": row["project_name"],
                    "L": row["likelihood"], "I": row["impact"],
                    "Status": row["status"],
                    "Review": row["review_date"].isoformat() if row["review_date"] else "-",
                    "Mitigation": (row["mitigation"] or "")[:60],
                }
                for row in risks
            ],
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("No RAID entries recorded.")

    with st.expander("Add a RAID entry"):
        with st.form("risk_create", clear_on_submit=True):
            project = st.selectbox("Project", list(project_names.values()), key="rk_project")
            category = st.selectbox("Category", registry.enum_values("risk", "category"))
            description = st.text_area("Description")
            likelihood = st.slider("Likelihood", 1, 5, 3)
            impact = st.slider("Impact", 1, 5, 3)
            mitigation = st.text_input("Mitigation")
            review = st.date_input("Review date", value=date.today())
            created = st.form_submit_button("Add", type="primary")
        if created and project_names:
            if not description.strip():
                st.warning("Description is required.")
            else:
                project_id = next(pid for pid, name in project_names.items() if name == project)
                result = dispatch_action(actions, settings, registry, "risk.create", person["person_id"], {
                    "project_id": project_id, "category": category, "description": description.strip(),
                    "likelihood": str(likelihood), "impact": str(impact),
                    "mitigation": mitigation or None, "review_date": review.isoformat(), "status": "open",
                })
                if result.get("status") == "completed":
                    st.success("Entry added.")
                    st.rerun()
                else:
                    st.error(result.get("error") or result.get("status"))

    if risks:
        with st.expander("Update an entry (owner-gated)"):
            labels = {f"[{row['category']}] {row['description'][:60]} ({row['status']})": str(row["risk_id"])
                      for row in risks}
            choice = st.selectbox("Entry", list(labels), key="rk_update")
            new_status = st.selectbox("New status", registry.enum_values("risk", "status"))
            mitigation = st.text_input("Mitigation (leave empty to keep)")
            if st.button("Submit update"):
                result = dispatch_action(actions, settings, registry, "risk.update", person["person_id"], {
                    "risk_id": labels[choice], "status": new_status, "mitigation": mitigation or None,
                })
                if result.get("__interrupt__"):
                    st.info("Update sent for owner approval (see Knowledge page queue).")
                elif result.get("status") == "completed":
                    st.success("Updated.")
                    st.rerun()
                else:
                    st.error(result.get("error") or result.get("status"))
