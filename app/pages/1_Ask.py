"""Ask: cited answers over the document index (Phase 1 app).

Every sentence carries a [S#] citation; anything without a supporting chunk
renders as "No source found." - unsourced claims are never paraphrased.
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

st.set_page_config(page_title="PPM Ask", layout="wide")

from ppm.config import get_settings                                          # noqa: E402
from ppm.workflows.rag import NO_SOURCE, RagWorkflow                         # noqa: E402

settings = get_settings()

st.title("Ask the document index")
st.caption(
    "Cited answers only - every sentence carries its source; "
    'unsupported claims render as "No source found."'
)

with st.form("ask"):
    question = st.text_input("Question", placeholder="What rate was used for reinforcement bar?")
    mode = st.radio(
        "Retrieval", ["auto", "literal"], horizontal=True,
        help="auto uses embeddings when a model is available; literal matches text exactly",
    )
    asked = st.form_submit_button("Ask", type="primary")

if asked and question.strip():
    try:
        result = RagWorkflow(settings).ask(question, mode=mode)
        st.session_state["rag_result"] = dict(result)
        st.session_state["rag_question"] = question
    except Exception as exc:
        st.error(f"ask failed: {exc}")
        st.info("Is Postgres running? scripts/dev_pg.ps1 start")

result = st.session_state.get("rag_result")
if result:
    if result.get("degraded"):
        st.warning(result["degraded"])
    answer = result.get("answer") or ""
    if answer.strip() == NO_SOURCE:
        st.info("No source found. Nothing in the index supports this question.")
    else:
        st.markdown(answer)

    chunks = result.get("chunks") or []
    if chunks:
        st.subheader(f"Sources ({len(chunks)})")
        for index, chunk in enumerate(chunks, start=1):
            similarity = f" | similarity {chunk['similarity']:.3f}" if chunk.get("similarity") is not None else ""
            st.markdown(f"**[S{index}]** `{chunk['source_path']}` - {chunk['section_ref']}{similarity}")
        labels = [f"[S{i}] {chunk['section_ref']}" for i, chunk in enumerate(chunks, start=1)]
        choice = st.selectbox("Chunk text", range(len(labels)), format_func=lambda i: labels[i])
        st.code(chunks[choice]["text_content"][:2000], language=None)
else:
    st.info("Ask a question. Answers cite the sheet range or page they came from.")
    try:
        from ppm import db

        counts = db.fetch_one(
            "SELECT (SELECT count(*) FROM document_chunk) AS chunks, "
            "(SELECT count(*) FROM document WHERE ingest_status = 'indexed') AS docs"
        )
        st.caption(f"index: {counts['chunks']} chunks from {counts['docs']} indexed document(s)")
    except Exception:
        st.caption("index not reachable yet")
