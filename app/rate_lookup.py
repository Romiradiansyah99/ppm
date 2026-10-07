"""PPM rate lookup - Phase 0 Streamlit UI (app_specs/rate-lookup.yaml).

Read-only. Views and filters come from the app spec; every result carries its
provenance; rates without sector/spec_level render flagged "UNCLASSIFIED"
(the spec's failsafe). Search history is private per user_label.

Run: .venv\\Scripts\\streamlit run app/rate_lookup.py
"""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

st.set_page_config(page_title="PPM Rate Lookup", page_icon=None, layout="wide")

from ppm.appspec import AppSpec, AppSpecError, load_app_spec          # noqa: E402
from ppm.config import Settings, ConfigError, get_settings            # noqa: E402
from ppm.registry import Registry, RegistryError, get_registry        # noqa: E402
from ppm.workflows.retrieval import RateQuery, rate_search            # noqa: E402

SPEC_FILE = "rate-lookup.yaml"


@st.cache_resource
def _boot() -> tuple[Settings, Registry, AppSpec]:
    settings = get_settings()
    registry = get_registry(str(settings.registry_dir))
    spec = load_app_spec(settings.app_specs_dir / SPEC_FILE, registry)
    return settings, registry, spec


def _idr(value: float | int | None) -> str:
    return "-" if value is None else "Rp " + f"{value:,.0f}".replace(",", ".")


def _search_history(settings: Settings, limit: int = 5) -> list[dict]:
    from ppm import db

    return db.fetch_all(
        "SELECT query, result_count, created_at FROM search_log "
        "WHERE user_label = %s ORDER BY created_at DESC LIMIT %s",
        (settings.user_label, limit),
    )


try:
    settings, registry, spec = _boot()
except (ConfigError, RegistryError, AppSpecError) as exc:
    st.error(f"PPM is not ready: {exc}")
    st.stop()

st.title("PPM Rate Lookup")
st.caption(
    f"Phase 0 - read-only, office-internal rates with provenance. "
    f"user: {spec.user} | owner: {spec.owner} | registry {registry.version} | provider {settings.provider}"
)

with st.sidebar:
    st.header("Filters")
    text = st.text_input("Description", placeholder="reinforcement bar, curtain wall, ...")
    sectors = sorted(set(registry.enum_values("project", "sector")))
    sector = st.selectbox("Sector", ["(any)"] + sectors)
    spec_level = st.selectbox("Spec level", ["(any)", "low", "medium", "high"])
    element_code = st.text_input("Element code", placeholder="e.g. C20")
    use_dates = st.checkbox("Date range")
    date_from = date_to = None
    if use_dates:
        date_from = st.date_input("From", date.today() - timedelta(days=5 * 365))
        date_to = st.date_input("To", date.today())
    limit = st.slider("Max results", 10, 200, 50)
    run = st.button("Search", type="primary", use_container_width=True)

    st.divider()
    st.caption(f"Filters and fields come from app_specs/{SPEC_FILE}.")
    st.caption(f"deprecation: {spec.deprecation_condition}")
    history = _search_history(settings)
    if history:
        st.divider()
        st.subheader("Your recent searches")
        for item in history:
            st.caption(f"{item['created_at']:%Y-%m-%d %H:%M}  {item['query'] or '(filters only)'}  -> {item['result_count']}")

if run or "rows" in st.session_state:
    if run:
        query = RateQuery(
            text=text or None,
            sector=None if sector == "(any)" else sector,
            spec_level=None if spec_level == "(any)" else spec_level,
            element_code=element_code or None,
            date_from=date_from if use_dates else None,
            date_to=date_to if use_dates else None,
            limit=limit,
        )
        try:
            rows, degraded = rate_search(query, settings)
        except Exception as exc:
            st.error(f"search failed: {exc}")
            st.info("Is Postgres running? scripts/dev_pg.ps1 start - then ppm init-db.")
            st.stop()
        st.session_state.rows = rows
        st.session_state.degraded = degraded
        st.session_state.query = query

    rows = st.session_state.rows
    degraded = st.session_state.get("degraded")

    if degraded:
        st.warning(degraded)
    if not rows:
        st.info(
            "No matching rates. If the truth layer is empty, index the samples first: "
            "`ppm ingest data/samples/*.xlsx`"
        )
    else:
        unclassified = sum(1 for row in rows if row["unclassified"])
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Results", len(rows))
        col_b.metric("Unclassified", unclassified)
        col_c.metric("Similarity ranking", "on" if rows[0].get("similarity") is not None else "off (filters only)")

        table = [
            {
                "Description": row["description"],
                "Code": row["element_code"],
                "Rate": _idr(row["rate_idr"]),
                "Unit": row["unit"],
                "Date": row["date_normalised_to"].isoformat(),
                "Sector": row["sector"] or "UNCLASSIFIED",
                "Spec": row["spec_level"] or "UNCLASSIFIED",
                "Similarity": round(row["similarity"], 3) if row.get("similarity") is not None else None,
                "Source": f"{row['project_name']} v{row['costplan_version']} - {row['source_ref']}",
            }
            for row in rows
        ]
        st.dataframe(table, use_container_width=True, hide_index=True)

        labels = [f"{row['description'][:60]} ({row['source_ref']})" for row in rows]
        choice = st.selectbox("Provenance detail", range(len(labels)), format_func=lambda i: labels[i])
        row = rows[choice]
        with st.expander("Provenance", expanded=True):
            st.markdown(
                f"**Rate** {_idr(row['rate_idr'])} /{row['unit']}  \n"
                f"**Project** {row['project_name']} (cost plan v{row['costplan_version']})  \n"
                f"**Document** `{row['source_path']}`  \n"
                f"**Hash** `{row['document_hash']}`  \n"
                f"**Source ref** `{row['source_ref']}`  \n"
                f"**Location** {row['location'] or '-'} | **client_identifiable** {row['client_identifiable']}  \n"
                f"**Rate basis** {row['rate_basis'] or 'unresolved'} | **Area basis** {row['area_basis'] or 'unresolved'}"
            )
            if row["unclassified"]:
                st.warning(f"Failsafe: {spec.failsafe}")
else:
    st.info("Set filters and search. Everything here is read-only.")
    try:
        from ppm import db

        total = db.fetch_one("SELECT count(*) AS n FROM benchmark_rate")
        documents = db.fetch_one("SELECT count(*) AS n FROM document WHERE ingest_status = 'indexed'")
        st.caption(f"truth layer: {total['n']} rates from {documents['n']} indexed document(s)")
    except Exception:
        st.caption("truth layer not reachable yet")
