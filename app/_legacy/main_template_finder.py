"""Streamlit entry point: Template Finder."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import streamlit as st

from ppm import db
from ppm.config import ConfigError, get_settings
from ppm.registry import load_taxonomy, validate_taxonomy
from ppm.workflows.retrieval import TemplateQuery, find_templates

st.set_page_config(page_title="PPM - Templates", layout="wide")

try:
    settings = get_settings()
    taxonomy = load_taxonomy(settings.taxonomy_path)
except (ConfigError, Exception) as exc:  # noqa: BLE001 - show setup errors in the UI
    st.error(f"PPM is not configured: {exc}")
    st.stop()

problems = validate_taxonomy(taxonomy, strict=False)
for problem in problems:
    st.warning(problem)

st.title("Template Finder")
st.caption("Office template library - deterministic search, no model involved.")

col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
text = col1.text_input("Search title or code", placeholder="risk register, cost plan, 601...")
stage = col2.selectbox("Stage", ["(all)"] + list(taxonomy.stages))
category = col3.selectbox("Category", ["(all)"] + list(taxonomy.categories))
file_format = col4.selectbox("Format", ["(all)"] + list(taxonomy.formats))
collection = col5.selectbox(
    "Collection",
    ["(all)", "idpm", "ttpm_flat", "ttpm_2020", "cm", "idcm", "supervision", "mom", "procedures"],
)

rows = find_templates(TemplateQuery(
    text=text or None,
    stage=None if stage == "(all)" else stage,
    category=None if category == "(all)" else category,
    format=None if file_format == "(all)" else file_format,
    collection=None if collection == "(all)" else collection,
    limit=500,
))

st.write(f"**{len(rows)} template(s)**")

if rows:
    table = [
        {
            "Code": row.get("code") or "-",
            "Title": row.get("title"),
            "Stage": row.get("stage"),
            "Category": row.get("category") or "other",
            "Format": row.get("format"),
            "Collection": row.get("source_collection"),
            "Content": "blank" if row.get("is_blank") else "sample",
        }
        for row in rows
    ]
    st.dataframe(table, width="stretch", hide_index=True)

    labels = {
        f"{row.get('code') or '-'} - {row.get('title')} ({row.get('format')}, {row.get('source_collection')})": row
        for row in rows
    }
    selected = st.selectbox("Detail", list(labels))
    row = labels[selected]
    left, right = st.columns(2)
    left.write(f"**Stage:** {row.get('stage')}")
    left.write(f"**Category:** {row.get('category') or 'other'}")
    left.write(f"**Licence:** {row.get('licence_class')}")
    left.write(f"**Ingest status:** {row.get('ingest_status') or 'catalog only'}")
    right.write(f"**Collection:** {row.get('source_collection')}")
    right.write(f"**Blank template:** {'yes' if row.get('is_blank') else 'contains sample content'}")
    right.write(f"**Document:** {row.get('document_id') or '-'}")

    source_path = row.get("source_path")
    st.code(source_path or "(no file - catalog only)", language=None)
    if source_path and Path(source_path).is_file():
        if st.button("Buka file"):
            os.startfile(source_path)  # noqa: S606 - local office machine, path from PPM DB
else:
    st.info("No template matched. Try a shorter search or clear a filter.")
