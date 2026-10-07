"""Shared Streamlit helpers: device-account login gate and action dispatch.

Pages never write entity tables directly - every write runs through the
compiled action graphs in ppm.workflows.actions.
"""

from __future__ import annotations

from contextlib import contextmanager

import streamlit as st

from ppm import auth, db
from ppm.config import get_settings
from ppm.registry import get_registry
from ppm.workflows.actions import ActionWorkflow, load_actions


def get_context():
    settings = get_settings()
    registry = get_registry(str(settings.registry_dir))
    actions = load_actions(registry, settings.app_specs_dir)
    return settings, registry, actions


def require_user() -> dict:
    person = st.session_state.get("person")
    if person:
        return person
    st.title("PPM")
    st.caption("Sign in with your office device account (local accounts; SSO is a later phase).")
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        person = auth.authenticate(username, password)
        if person is None:
            st.error("Invalid username or password.")
        else:
            st.session_state["person"] = person
            st.rerun()
    st.stop()


def user_badge() -> None:
    person = st.session_state.get("person")
    if not person:
        return
    st.sidebar.caption(f"Signed in as **{person['display_name']}** ({person['role']})")
    if st.sidebar.button("Sign out"):
        st.session_state.pop("person", None)
        st.rerun()


@contextmanager
def _compiled(workflow: ActionWorkflow, settings):
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(settings.dsn) as checkpointer:
        workflow.compile(checkpointer)
        yield workflow


def dispatch_action(actions, settings, registry, name: str, actor_id: str, params: dict) -> dict:
    """Run a compiled action; a gated run pauses in the approval queue."""
    workflow = ActionWorkflow(actions[name], registry, settings)
    with _compiled(workflow, settings):
        result = workflow.run(actor_id, params)
    if result.get("__interrupt__"):
        db.execute(
            "UPDATE action_run SET status = 'awaiting_approval', updated_at = now() WHERE thread_id = %s",
            (result["thread_id"],),
        )
    return result


def resolve_pending(actions, settings, registry, action_name: str, thread_id: str, decision: dict) -> dict:
    workflow = ActionWorkflow(actions[action_name], registry, settings)
    with _compiled(workflow, settings):
        return workflow.resume(thread_id, decision)
