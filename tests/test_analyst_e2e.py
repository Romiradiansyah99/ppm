from pathlib import Path

import pandas as pd
import pytest

from ppm import db
from ppm.analyst.demo import demo_codegen, demo_plan
from ppm.analyst.executor import run_plan
from ppm.data.seed import seed_defaults

pytestmark = pytest.mark.db


def test_demo_plan_end_to_end_and_cache(clean_db):
    seed_defaults()

    first = run_plan(
        demo_plan(),
        user_label="test",
        codegen=demo_codegen(),
        approved=True,
        model="static-demo",
        access_level="all",
    )
    assert first.status == "completed", first.error
    assert first.report_path and Path(first.report_path).is_file()

    artifacts = db.fetch_all(
        "SELECT kind, name, path FROM analysis_artifact WHERE run_id = %s", (first.run_id,)
    )
    kinds = {row["kind"] for row in artifacts}
    assert {"dataframe", "chart", "report"} <= kinds

    report = Path(first.report_path).read_text(encoding="utf-8")
    assert "Provenance" in report
    assert "Top components" in report

    top = next(row for row in artifacts if row["kind"] == "dataframe" and "top_components" in row["name"])
    frame = pd.read_parquet(top["path"])
    assert len(frame) > 0
    assert "line_total" in frame.columns

    second = run_plan(
        demo_plan(),
        user_label="test",
        codegen=demo_codegen(),
        approved=True,
        model="static-demo",
        access_level="all",
    )
    assert second.status == "completed", second.error
    tasks = db.fetch_all(
        "SELECT status FROM analysis_task WHERE run_id = %s", (second.run_id,)
    )
    assert tasks and all(row["status"] == "cached" for row in tasks)
