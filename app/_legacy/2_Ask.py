"""Streamlit page: cited answers over the corpus."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

import streamlit as st

from ppm.config import ConfigError, get_settings
from ppm.workflows.rag import AskWorkflow

st.set_page_config(page_title="PPM - Ask", layout="wide")

try:
    settings = get_settings()
except ConfigError as exc:
    st.error(f"PPM is not configured: {exc}")
    st.stop()

st.title("Ask the corpus")
st.caption(
    "Answers cite the file and section they came from. "
    "If the corpus does not contain the answer, the system says so."
)

with st.form("ask"):
    question = st.text_area("Question", height=100)
    left, right = st.columns(2)
    collection = left.selectbox(
        "Collection",
        ["(all)", "idpm", "ttpm_flat", "ttpm_2020", "cm", "idcm", "supervision", "mom", "procedures"],
    )
    mode = right.radio("Mode", ["auto", "literal"], horizontal=True)
    submitted = st.form_submit_button("Ask")

if submitted and question.strip():
    with st.spinner("Retrieving and checking citations..."):
        result = AskWorkflow(settings).ask(
            question.strip(),
            collection=None if collection == "(all)" else collection,
            mode=mode,
        )
    st.session_state["last_answer"] = result

result = st.session_state.get("last_answer")
if result:
    st.subheader("Answer")
    st.markdown(result.get("answer") or "No source found.")
    if result.get("degraded"):
        st.info(f"Degraded: {result['degraded']}")
    stats = result.get("stats") or {}
    if stats.get("refused"):
        st.warning(f"Refused: {stats.get('reason')}")

    chunks = result.get("chunks") or []
    if chunks:
        st.subheader("Sources")
        for index, chunk in enumerate(chunks, start=1):
            title = Path(str(chunk.get("source_path", ""))).name
            section = chunk.get("section_ref")
            with st.expander(f"[S{index}] {title} - {section}"):
                st.write(f"**Path:** {chunk.get('source_path')}")
                st.write(f"**Collection:** {chunk.get('source_collection')}")
                st.write(f"**Similarity:** {chunk.get('similarity')}")
                if chunk.get("is_blank"):
                    st.caption("Blank template - no project data inside.")
                st.text(str(chunk.get("text_content", ""))[:4000])
