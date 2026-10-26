"""Streamlit page: PPM Analyst (plan -> approve -> run -> artifacts)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

import pandas as pd
import streamlit as st

from ppm import db
from ppm.analyst.artifacts import list_artifacts
from ppm.analyst.codegen import OpenAICompatibleCodegen
from ppm.analyst.demo import demo_codegen, demo_plan
from ppm.analyst.executor import run_plan
from ppm.analyst.planner import plan_question
from ppm.analyst.schemas import AnalysisPlan
from ppm.analyst.teach import propose_lesson, record_teaching
from ppm.config import ConfigError, get_settings
from ppm.data.report import build_report

st.set_page_config(page_title="PPM - Analyst", layout="wide")

try:
    settings = get_settings()
except ConfigError as exc:
    st.error(f"PPM is not configured: {exc}")
    st.stop()

api_ready = bool(
    settings.residency == "api_approved"
    and settings.llm_api_base
    and settings.llm_api_key
    and settings.llm_model
    and settings.analyst_egress_ack
)

st.title("PPM Analyst")
st.caption(
    "Bridgewater PAT-style loop: clarify -> plan (datasets + DAG) -> sandboxed code -> "
    "deterministic validation -> report. Numbers come from artifacts, not prose."
)

if not api_ready:
    st.warning(
        "Planner/codegen API belum siap. Isi di `.env`: PPM_RESIDENCY=api_approved, "
        "PPM_LLM_API_BASE, PPM_LLM_API_KEY, PPM_LLM_MODEL, PPM_ANALYST_EGRESS_ACK. "
        "Demo deterministik tetap bisa dijalankan tanpa API."
    )
if settings.residency == "api_approved":
    st.info("Analyst memakai API eksternal: pertanyaan, schema, dan sample rows keluar dari kantor.")

st.subheader("Data coverage")
coverage = build_report()
cols = st.columns(len(coverage["counts"]))
for column, (table, count) in zip(cols, coverage["counts"].items()):
    column.metric(table.replace("_", " "), count)
left, right = st.columns(2)
with left:
    st.caption("Currencies")
    st.dataframe(coverage["currencies"], width="stretch", hide_index=True)
with right:
    st.caption("Reconciliation mismatches")
    st.metric("mismatches", coverage["reconciliation_mismatches"])

tab_ask, tab_run, tab_history, tab_teach = st.tabs(["Ask", "Demo", "Runs", "Teach"])


def _show_run(run_id: str) -> None:
    run = db.fetch_one("SELECT * FROM analysis_run WHERE run_id = %s", (run_id,))
    if not run:
        st.error("run not found")
        return
    a, b, c = st.columns(3)
    a.metric("Status", run["status"])
    b.metric("Model", run["model"] or "-")
    c.metric("Total", f"{(run['total_ms'] or 0) / 1000:.1f} s")
    if run["error"]:
        st.error(run["error"])
    st.code(run["question"], language=None)

    tasks = db.fetch_all(
        "SELECT task_key, name, kind, status, duration_ms, output_path, validation, error "
        "FROM analysis_task WHERE run_id = %s ORDER BY started_at NULLS LAST, task_key",
        (run_id,),
    )
    if tasks:
        st.dataframe(
            [
                {
                    "task": row["task_key"],
                    "kind": row["kind"],
                    "status": row["status"],
                    "ms": row["duration_ms"],
                    "output": row["output_path"],
                    "error": row["error"],
                }
                for row in tasks
            ],
            width="stretch",
            hide_index=True,
        )

    for artifact in list_artifacts(run_id):
        st.markdown(f"**{artifact['kind']}** - `{artifact['name']}`")
        path = Path(artifact["path"]) if artifact["path"] else None
        if not path or not path.is_file():
            st.caption("file missing")
            continue
        if artifact["kind"] == "dataframe":
            st.dataframe(pd.read_parquet(path), width="stretch", hide_index=True)
        elif artifact["kind"] == "chart":
            st.image(str(path), width="stretch")
        elif artifact["kind"] == "report":
            st.markdown(path.read_text(encoding="utf-8"))


with tab_ask:
    question = st.text_area(
        "Question",
        placeholder=(
            "Contoh: komponen biaya apa yang paling besar lintas proyek benchmark, "
            "dan berapa rate per m2-nya?"
        ),
        height=90,
    )
    plan_col, demo_col = st.columns(2)
    if plan_col.button("Buat Plan", disabled=not api_ready, type="primary"):
        try:
            plan = plan_question(question, settings)
            st.session_state["analyst_plan"] = plan.model_dump(by_alias=True)
            st.session_state["analyst_question"] = question
            st.session_state["analyst_error"] = None
        except Exception as exc:
            st.session_state["analyst_error"] = str(exc)
    if demo_col.button("Jalankan demo cost benchmark"):
        result = run_plan(
            demo_plan(), user_label=settings.user_label,
            codegen=demo_codegen(), approved=True, model="static-demo",
        )
        st.session_state["analyst_run_id"] = result.run_id
        st.session_state["analyst_error"] = result.error

    if st.session_state.get("analyst_error"):
        st.error(st.session_state["analyst_error"])

    plan_payload = st.session_state.get("analyst_plan")
    if plan_payload:
        plan = AnalysisPlan.model_validate(plan_payload)
        st.subheader("Plan review")
        st.write("**Assumptions**")
        for assumption in plan.assumptions:
            st.write(f"- {assumption}")
        st.write("**Datasets**")
        for dataset in plan.datasets:
            with st.expander(f"{dataset.name} ({dataset.source})"):
                st.code(dataset.sql or dataset.artifact_id or "", language="sql")
                st.json(dataset.schema_)
        st.write("**Tasks**")
        st.dataframe(
            [
                {
                    "id": task.id, "name": task.name, "kind": task.kind,
                    "depends_on": ", ".join(task.depends_on),
                    "validation": ", ".join(item.check for item in task.validation),
                }
                for task in plan.tasks
            ],
            width="stretch",
            hide_index=True,
        )
        approve, reject = st.columns(2)
        if approve.button("Approve & Run", type="primary", disabled=not api_ready):
            result = run_plan(
                plan,
                user_label=settings.user_label,
                codegen=OpenAICompatibleCodegen(settings),
                approved=True,
                model=settings.llm_model,
            )
            st.session_state["analyst_run_id"] = result.run_id
            st.session_state["analyst_error"] = result.error
            st.session_state["analyst_plan"] = None
        if reject.button("Reject"):
            st.session_state["analyst_plan"] = None

with tab_run:
    st.write(
        "Demo deterministik memakai plan yang sama dengan test: 37 cost line, "
        "10 proyek benchmark, validasi rekonsiliasi, chart, dan report."
    )
    if st.button("Run demo"):
        result = run_plan(
            demo_plan(), user_label=settings.user_label,
            codegen=demo_codegen(), approved=True, model="static-demo",
        )
        st.session_state["analyst_run_id"] = result.run_id
        st.session_state["analyst_error"] = result.error

with tab_history:
    runs = db.fetch_all(
        "SELECT run_id, status, model, question, total_ms, created_at "
        "FROM analysis_run ORDER BY created_at DESC LIMIT 20"
    )
    if not runs:
        st.caption("No runs yet.")
    else:
        labels = {
            f"{row['created_at']:%Y-%m-%d %H:%M} | {row['status']:<9} | {row['question'][:70]}": row["run_id"]
            for row in runs
        }
        selected = st.selectbox("Run", list(labels))
        st.session_state["analyst_run_id"] = labels[selected]

with tab_teach:
    run_id = st.session_state.get("analyst_run_id")
    st.write(f"Run aktif: `{run_id or 'belum ada'}`")
    note = st.text_area(
        "Apa yang salah atau kurang tepat?",
        placeholder="Contoh: rate per m2 seharusnya memakai NIA, bukan GFA.",
        height=100,
    )
    if st.button("Simpan koreksi + usulkan lesson", disabled=not run_id or not note.strip()):
        event_id = record_teaching(run_id, note.strip())
        lesson = propose_lesson(event_id, settings)
        st.success(f"teaching event: {event_id}")
        st.write(lesson)
        st.caption("Lesson disimpan sebagai proposal. Tidak ada perubahan harness otomatis.")

run_id = st.session_state.get("analyst_run_id")
if run_id:
    st.subheader("Run")
    _show_run(run_id)
