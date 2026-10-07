"""Knowledge: private lessons, the shared layer, approvals, competencies,
and the private memory store (Phase 2 app)."""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
st.set_page_config(page_title="PPM Knowledge", layout="wide")

from ppm import db                                                           # noqa: E402
from ui_common import (                                                      # noqa: E402
    dispatch_action,
    get_context,
    require_user,
    resolve_pending,
    user_badge,
)

person = require_user()
user_badge()
settings, registry, actions = get_context()

st.title("Knowledge")
st.caption("Private lessons first; approvers promote the reusable ones to the shared layer.")

tab_mine, tab_shared, tab_queue, tab_competency, tab_memory = st.tabs(
    ["My lessons", "Shared layer", "Approvals", "Competencies", "Private memory"]
)

# --- my lessons -----------------------------------------------------------------
with tab_mine:
    with st.form("lesson_form", clear_on_submit=True):
        text = st.text_area("Lesson", placeholder="What would you tell the next person?")
        col_a, col_b = st.columns(2)
        sector = col_a.selectbox("Sector", ["(none)"] + registry.enum_values("lesson_learned", "sector"))
        stage = col_b.selectbox("Stage", ["(none)"] + registry.enum_values("lesson_learned", "stage"))
        submitted = st.form_submit_button("Submit lesson", type="primary")
    if submitted:
        if not text.strip():
            st.warning("Write the lesson first.")
        else:
            result = dispatch_action(actions, settings, registry, "lesson.submit", person["person_id"], {
                "text_content": text.strip(),
                "sector": None if sector == "(none)" else sector,
                "stage": None if stage == "(none)" else stage,
            })
            if result.get("status") == "completed":
                st.success("Lesson saved privately. Promote it when it is reusable.")
            else:
                st.error(result.get("error") or result.get("status"))

    mine = db.fetch_all(
        "SELECT lesson_id, text_content, status, layer, trust_weight, updated_at "
        "FROM lesson_learned WHERE author_id = %s ORDER BY updated_at DESC",
        (person["person_id"],),
    )
    if mine:
        st.dataframe(
            [
                {
                    "Lesson": row["text_content"][:80],
                    "Status": row["status"],
                    "Layer": row["layer"],
                    "Weight": float(row["trust_weight"]),
                    "Updated": row["updated_at"].strftime("%Y-%m-%d"),
                }
                for row in mine
            ],
            use_container_width=True, hide_index=True,
        )
        promotable = [row for row in mine if row["status"] in {"draft", "rejected"} and row["layer"] == "private"]
        if promotable:
            labels = {f"{row['text_content'][:60]} ({row['status']})": row["lesson_id"] for row in promotable}
            choice = st.selectbox("Request promotion to the shared layer", list(labels))
            if st.button("Request promotion (approver decides)"):
                result = dispatch_action(actions, settings, registry, "lesson.promote", person["person_id"],
                                         {"lesson_id": str(labels[choice])})
                if result.get("__interrupt__"):
                    st.info(f"Sent to the approver queue (run {result['thread_id'][:8]}...).")
                elif result.get("status") == "completed":
                    st.success("Promoted.")
                else:
                    st.error(result.get("error") or result.get("status"))
    else:
        st.info("No lessons yet - the first one is usually the most uncomfortable.")

# --- shared layer -----------------------------------------------------------------
with tab_shared:
    sector_filter = st.selectbox("Filter sector", ["(any)"] + registry.enum_values("lesson_learned", "sector"))
    query = (
        "SELECT l.text_content, l.sector, l.stage, l.trust_weight, l.updated_at, p.display_name "
        "FROM lesson_learned l JOIN person p ON p.person_id = l.author_id "
        "WHERE l.status = 'shared' AND l.layer = 'shared'"
    )
    params = []
    if sector_filter != "(any)":
        query += " AND l.sector = %s"
        params.append(sector_filter)
    query += " ORDER BY l.trust_weight DESC, l.updated_at DESC LIMIT 200"
    shared = db.fetch_all(query, params)
    if shared:
        st.dataframe(
            [
                {
                    "Lesson": row["text_content"][:120],
                    "Sector": row["sector"] or "unclassified",
                    "Stage": row["stage"] or "unclassified",
                    "Weight": float(row["trust_weight"]),
                    "Author": row["display_name"],
                    "Updated": row["updated_at"].strftime("%Y-%m-%d"),
                }
                for row in shared
            ],
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("The shared layer is empty so far.")

# --- approvals -----------------------------------------------------------------------
with tab_queue:
    pending = db.fetch_all(
        "SELECT ar.run_id, ar.thread_id, ar.action, ar.params, ar.created_at, p.display_name AS actor "
        "FROM action_run ar LEFT JOIN person p ON p.person_id = ar.actor_id "
        "WHERE ar.status = 'awaiting_approval' ORDER BY ar.created_at"
    )
    if not pending:
        st.info("Nothing waiting for approval.")
    elif not person["is_approver"]:
        st.warning(f"{len(pending)} item(s) wait for an approver - you are not one.")
    else:
        for run in pending:
            with st.expander(f"{run['action']} by {run['actor'] or '?'} ({run['created_at']:%Y-%m-%d %H:%M})"):
                st.json(run["params"])
                note = st.text_input("Note", key=f"note_{run['run_id']}")
                col_a, col_b = st.columns(2)
                if col_a.button("Approve", key=f"approve_{run['run_id']}", type="primary"):
                    result = resolve_pending(actions, settings, registry, run["action"], run["thread_id"],
                                             {"action": "approve", "approver_id": person["person_id"], "note": note})
                    if result.get("status") == "completed":
                        st.success("Approved.")
                        st.rerun()
                    else:
                        st.error(result.get("error") or result.get("status"))
                if col_b.button("Reject", key=f"reject_{run['run_id']}"):
                    result = resolve_pending(actions, settings, registry, run["action"], run["thread_id"],
                                             {"action": "reject", "approver_id": person["person_id"], "note": note})
                    if result.get("status") == "rejected":
                        st.warning("Rejected.")
                        st.rerun()
                    else:
                        st.error(result.get("error") or result.get("status"))

# --- competencies ----------------------------------------------------------------------
with tab_competency:
    mine_comp = db.fetch_all(
        "SELECT c.name, c.pillar, c.level, pc.status, c.evidence_required "
        "FROM person_competency pc JOIN competency_standard c ON c.competency_id = pc.competency_id "
        "WHERE pc.person_id = %s ORDER BY c.pillar, c.name",
        (person["person_id"],),
    )
    if mine_comp:
        st.dataframe(mine_comp, use_container_width=True, hide_index=True)
    else:
        st.info("No competencies claimed yet. Standards live in competency_standard (Phase 2 seed).")
    standards = db.fetch_all("SELECT pillar, name, level, description FROM competency_standard ORDER BY pillar, name, level LIMIT 200")
    if standards:
        st.subheader("Standards")
        st.dataframe(standards, use_container_width=True, hide_index=True)

# --- private memory ----------------------------------------------------------------------
with tab_memory:
    st.caption("Private to you - never shown to anyone else (memory_scope: private).")
    rows = db.fetch_all(
        "SELECT kind, content, updated_at FROM private_memory WHERE person_id = %s ORDER BY kind",
        (person["person_id"],),
    )
    for row in rows:
        st.markdown(f"**{row['kind']}** ({row['updated_at']:%Y-%m-%d})")
        st.json(row["content"])
    with st.form("memory_form", clear_on_submit=True):
        kind = st.text_input("Kind", placeholder="writing_style / search_habits / ...")
        content = st.text_area("Content (JSON)", placeholder='{"tone": "concise"}')
        saved = st.form_submit_button("Save")
    if saved and kind.strip():
        import json

        try:
            parsed = json.loads(content or "{}")
        except json.JSONDecodeError:
            st.error("Content must be valid JSON.")
        else:
            db.execute(
                "INSERT INTO private_memory (person_id, kind, content) VALUES (%s, %s, %s) "
                "ON CONFLICT (person_id, kind) DO UPDATE SET content = EXCLUDED.content, updated_at = now()",
                (person["person_id"], kind.strip(), json.dumps(parsed)),
            )
            st.success("Saved.")
            st.rerun()
