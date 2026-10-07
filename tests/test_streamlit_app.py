"""Streamlit app smoke tests with streamlit.testing.v1.AppTest: the pages
gate on login, then boot, render, and survive a search interaction."""

from __future__ import annotations

from ppm.config import REPO_ROOT
from tests.conftest import requires_db

APP_FILE = REPO_ROOT / "app" / "rate_lookup.py"

TEST_PERSON = {
    "person_id": "00000000-0000-0000-0000-000000000001",
    "username": "tester",
    "display_name": "Tester",
    "role": "consultant",
    "is_approver": False,
}


def _login(app):
    app.session_state["person"] = dict(TEST_PERSON)


def _click_search(app):
    button = next(button for button in app.sidebar.button if button.label == "Search")
    button.click()


@requires_db
def test_app_gates_on_login(prepared_db):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(APP_FILE), default_timeout=60)
    app.run()
    assert not app.exception
    assert app.title  # login screen shows the PPM title


@requires_db
def test_app_boots_and_searches(prepared_db):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(APP_FILE), default_timeout=60)
    _login(app)
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]

    # the spec drives the sidebar: a search with filters must not blow up
    assert app.sidebar.text_input, "expected the description filter"
    app.sidebar.text_input[0].set_value("reinforcement")
    _click_search(app)
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]


@requires_db
def test_app_renders_results_when_rates_exist(samples, registry, settings, clean_db):
    """Index beta with the stub provider, then search through the UI."""
    from dataclasses import replace

    from langgraph.checkpoint.postgres import PostgresSaver
    from streamlit.testing.v1 import AppTest

    from ppm.workflows.ingest import IngestWorkflow

    stub = replace(settings, provider="stub")
    workflow = IngestWorkflow(stub, registry)
    with PostgresSaver.from_conn_string(stub.dsn) as checkpointer:
        workflow.compile(checkpointer)
        result = workflow.run(str(samples["beta"]))
    assert result["status"] == "completed"

    app = AppTest.from_file(str(APP_FILE), default_timeout=60)
    _login(app)
    app.run()
    app.sidebar.text_input[0].set_value("electrical")
    _click_search(app)
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert app.dataframe, "expected the results table"
