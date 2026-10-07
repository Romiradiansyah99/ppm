"""Streamlit app smoke test with streamlit.testing.v1.AppTest: the app boots,
renders, and survives a search interaction without exceptions."""

from __future__ import annotations

from ppm.config import REPO_ROOT
from tests.conftest import requires_db

APP_FILE = REPO_ROOT / "app" / "rate_lookup.py"


@requires_db
def test_app_boots_and_searches(prepared_db):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(APP_FILE), default_timeout=60)
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]

    # the spec drives the sidebar: a search with filters must not blow up
    assert app.sidebar.text_input, "expected the description filter"
    app.sidebar.text_input[0].set_value("reinforcement")
    app.sidebar.button[0].click()
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
    app.run()
    app.sidebar.text_input[0].set_value("electrical")
    app.sidebar.button[0].click()
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert app.dataframe, "expected the results table"
