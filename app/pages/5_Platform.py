"""Platform: every approved app spec, compiled and runnable (Phase 6).

Views compile to SQL; actions compile to gated graphs. This page is the
generic runner - it renders whatever the app_specs/ directory declares.
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
st.set_page_config(page_title="PPM Platform", layout="wide")

from ppm import db                                                           # noqa: E402
from ppm.appspec import AppSpecError, load_app_spec                          # noqa: E402
from ppm.compiler import CompileError, run_view                              # noqa: E402
from ppm.workflows.actions import ActionWorkflow                             # noqa: E402
from ui_common import (                                                      # noqa: E402
    dispatch_action,
    get_context,
    require_user,
    user_badge,
)

person = require_user()
user_badge()
settings, registry, actions = get_context()

st.title("Platform")
st.caption("Approved app specs, compiled at load time: views become SQL, actions become gated graphs.")

specs = []
for path in sorted(settings.app_specs_dir.glob("*.yaml")):
    try:
        specs.append((path, load_app_spec(path, registry)))
    except AppSpecError as exc:
        st.warning(f"{path.name}: broken spec - {exc}")

if not specs:
    st.info("No approved apps yet. Draft one: ppm apps draft \"...\"")
    st.stop()

labels = {spec.name: (path, spec) for path, spec in specs}
choice = st.selectbox("App", sorted(labels))
path, spec = labels[choice]
st.caption(f"spec: {path.name} | user: {spec.user} | owner: {spec.owner} | "
           f"memory: {spec.memory_scope} | deprecation: {spec.deprecation_condition}")

app_actions = {name: action for name, action in actions.items() if action.app == spec.name}

tab_labels = [view.name for view in spec.views] + (["Actions"] if app_actions else [])
tabs = st.tabs(tab_labels)

for tab, view in zip(tabs, spec.views):
    with tab:
        filter_values = {}
        if view.filters:
            columns = st.columns(min(len(view.filters), 4))
            for index, filter_name in enumerate(view.filters):
                filter_values[filter_name] = columns[index % len(columns)].text_input(
                    filter_name, key=f"{spec.name}_{view.name}_{filter_name}"
                )
        try:
            rows = run_view(view, registry, filters=filter_values, person_id=person["person_id"])
        except CompileError as exc:
            st.error(f"view does not compile: {exc}")
            rows = []
        except Exception as exc:
            st.error(f"view failed: {exc}")
            rows = []
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
            st.caption(f"{len(rows)} row(s)")
        else:
            st.info("No rows match.")

if app_actions and len(tabs) > len(spec.views):
    with tabs[-1]:
        st.caption("Actions run as compiled graphs; gated actions pause in the approvals queue.")
        action_name = st.selectbox("Action", sorted(app_actions))
        action = app_actions[action_name]
        st.caption(f"{action.operation} into {action.entity} - gate: {action.gate}")
        with st.form(f"platform_action_{action_name}"):
            params = {}
            for field_name in action.fields:
                required = field_name in action.required
                params[field_name] = st.text_input(
                    field_name + (" *" if required else ""), key=f"pf_{action_name}_{field_name}"
                )
            submitted = st.form_submit_button("Run", type="primary")
        if submitted:
            clean = {key: value for key, value in params.items() if value != ""}
            result = dispatch_action(actions, settings, registry, action_name, person["person_id"], clean)
            if result.get("__interrupt__"):
                st.info(f"Awaiting approval (gate: {action.gate}) - see the Knowledge page queue.")
            elif result.get("status") == "completed":
                st.success("Done.")
                st.json(result.get("result") or {})
            else:
                st.error(result.get("error") or result.get("status"))
