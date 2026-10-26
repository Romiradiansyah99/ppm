"""Streamlit page: corpus health and review queue."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

import streamlit as st

from ppm import db
from ppm.config import ConfigError, get_settings

st.set_page_config(page_title="PPM - Corpus", layout="wide")

try:
    settings = get_settings()
except ConfigError as exc:
    st.error(f"PPM is not configured: {exc}")
    st.stop()

st.title("Corpus health")
st.caption("What is indexed, what is excluded, and what is waiting for a human.")

status_rows = db.fetch_all(
    "SELECT ingest_status, count(*) AS count FROM document GROUP BY ingest_status ORDER BY ingest_status"
)
licence_rows = db.fetch_all(
    "SELECT licence_class, count(*) AS count FROM document GROUP BY licence_class ORDER BY licence_class"
)
collection_rows = db.fetch_all(
    "SELECT source_collection, count(*) AS count FROM document GROUP BY source_collection ORDER BY source_collection"
)
chunk_count = db.fetch_one("SELECT count(*) AS count FROM document_chunk")["count"]
template_count = db.fetch_one("SELECT count(*) AS count FROM template")["count"]
embedded = db.fetch_one(
    "SELECT count(*) AS count FROM document_chunk WHERE embedding IS NOT NULL"
)["count"]

st.subheader("Totals")
a, b, c = st.columns(3)
a.metric("Templates", template_count)
b.metric("Chunks", chunk_count)
c.metric("Embedded chunks", embedded)

left, right = st.columns(2)
with left:
    st.subheader("Documents by status")
    st.dataframe(status_rows, width="stretch", hide_index=True)
    st.subheader("By collection")
    st.dataframe(collection_rows, width="stretch", hide_index=True)
with right:
    st.subheader("By licence")
    st.dataframe(licence_rows, width="stretch", hide_index=True)

st.subheader("Review queue")
queue = db.fetch_all(
    "SELECT thread_id, source_path, stats FROM ingest_run "
    "WHERE status = 'awaiting_review' ORDER BY created_at"
)
if not queue:
    st.success("Nothing waiting for review.")
else:
    for item in queue:
        payload = (item.get("stats") or {}).get("review") or {}
        with st.expander(f"{Path(item['source_path']).name} - {item['thread_id']}"):
            st.write(f"**Path:** {item['source_path']}")
            st.write(f"**Reasons:** {', '.join(payload.get('reasons') or []) or '-'}")
            st.write(f"**Redaction hits:** {payload.get('redaction_hits') or 'none'}")
            st.write(f"**Parse:** {payload.get('parse') or {}}")
            st.code(
                f'ppm review approve {item["thread_id"]} --licence office_authored\n'
                f'ppm review reject {item["thread_id"]}',
                language="powershell",
            )

st.subheader("Not indexed")
not_indexed = db.fetch_all(
    "SELECT source_path, ingest_status, stats FROM document "
    "WHERE ingest_status IN ('reference_only', 'forbidden', 'failed') ORDER BY source_path"
)
if not_indexed:
    st.dataframe(
        [
            {
                "Path": row["source_path"],
                "Status": row["ingest_status"],
                "Reason": (row.get("stats") or {}).get("reason", ""),
            }
            for row in not_indexed
        ],
        width="stretch",
        hide_index=True,
    )
else:
    st.caption("No reference-only or failed documents.")
