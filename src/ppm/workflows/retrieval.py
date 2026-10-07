"""Rate lookup: hybrid retrieval behind one small interface.

SQL filters first, vector similarity second (plan section 9). Embeddings live
on the benchmark_rate row itself - framework-neutral - and the only LangChain
touchpoint is a BaseRetriever adapter at the bottom, so the implementation can
be swapped without touching callers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ppm import db
from ppm.config import Settings, get_settings
from ppm.models import ModelUnavailable, embed_query

_SELECT = """
SELECT r.rate_id, r.description, r.element_code, r.rate_idr, r.unit, r.date_normalised_to,
       r.sector, r.spec_level, r.location, r.source_ref, r.client_identifiable,
       c.version AS costplan_version, c.rate_basis, c.area_basis,
       p.name AS project_name,
       d.document_id, d.source_path, d.hash AS document_hash
       {similarity}
FROM benchmark_rate r
JOIN cost_plan c ON c.costplan_id = r.costplan_id
JOIN project p ON p.project_id = c.project_id
JOIN document d ON d.document_id = r.document_id
WHERE TRUE
"""


@dataclass(frozen=True)
class RateQuery:
    text: str | None = None
    sector: str | None = None
    spec_level: str | None = None
    element_code: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    limit: int = 50

    def filters_dict(self) -> dict:
        return {
            "sector": self.sector,
            "spec_level": self.spec_level,
            "element_code": self.element_code,
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
        }


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_search_sql(query: RateQuery, has_vector: bool, text_like: str | None = None) -> tuple[str, list[Any]]:
    """Pure SQL builder - unit tested without a database.

    text_like is the degraded text mode: when embeddings are unavailable the
    term is matched literally (ILIKE) instead of being silently ignored.
    """
    params: list[Any] = []
    sql = _SELECT.format(
        similarity=", (1 - (r.embedding <=> %s::vector)) AS similarity" if has_vector else ", NULL AS similarity"
    )
    if has_vector:
        params.append(None)  # placeholder position; the vector is bound by the caller

    if text_like:
        sql += " AND r.description ILIKE %s"
        params.append(f"%{_escape_like(text_like)}%")
    if query.sector:
        sql += " AND r.sector = %s"
        params.append(query.sector)
    if query.spec_level:
        sql += " AND r.spec_level = %s"
        params.append(query.spec_level)
    if query.element_code:
        sql += " AND r.element_code = %s"
        params.append(query.element_code)
    if query.date_from:
        sql += " AND r.date_normalised_to >= %s"
        params.append(query.date_from)
    if query.date_to:
        sql += " AND r.date_normalised_to <= %s"
        params.append(query.date_to)
    if has_vector:
        sql += " AND r.embedding IS NOT NULL"

    sql += " ORDER BY similarity DESC" if has_vector else " ORDER BY r.date_normalised_to DESC, r.rate_idr DESC"
    sql += " LIMIT %s"
    params.append(query.limit)
    return sql, params


def _bind_vector(sql: str, params: list[Any], vector: list[float]) -> list[Any]:
    """Replace the leading None placeholder with the actual vector."""
    bound = list(params)
    if bound and bound[0] is None:
        bound[0] = vector
    return bound


def rate_search(
    query: RateQuery,
    settings: Settings | None = None,
    *,
    log_user: str | None = None,
    log: bool = True,
) -> tuple[list[dict], str | None]:
    """Run the hybrid search. Returns (rows, degraded_reason).

    degraded_reason is set when vector similarity was requested but the
    embedder was unavailable - the term is then matched literally (ILIKE).
    """
    settings = settings or get_settings()
    vector: list[float] | None = None
    degraded: str | None = None

    if query.text:
        try:
            vector = embed_query(settings, query.text)
        except ModelUnavailable as exc:
            degraded = f"vector similarity off ({exc}); matched literally instead"

    sql, params = build_search_sql(
        query, has_vector=vector is not None, text_like=query.text if vector is None else None
    )
    if vector is not None:
        params = _bind_vector(sql, params, vector)
    rows = db.fetch_all(sql, params)

    for row in rows:
        row["rate_id"] = str(row["rate_id"])
        row["document_id"] = str(row["document_id"])
        row["unclassified"] = not (row.get("sector") and row.get("spec_level"))

    if log:
        user = log_user or settings.user_label
        try:
            db.execute(
                "INSERT INTO search_log (user_label, query, filters, result_count) VALUES (%s, %s, %s, %s)",
                (user, query.text, json.dumps(query.filters_dict()), len(rows)),
            )
        except Exception:
            pass  # instrumentation must never break a search

    return rows, degraded


# --- LangChain adapter (the framework touchpoint stays thin) ------------------

def as_retriever(settings: Settings | None = None, **filters: Any):
    from langchain_core.documents import Document
    from langchain_core.retrievers import BaseRetriever

    settings = settings or get_settings()

    class RateLookupRetriever(BaseRetriever):
        def _get_relevant_documents(self, query: str, *, run_manager=None) -> list[Document]:
            rows, _ = rate_search(RateQuery(text=query, **filters), settings, log=False)
            return [
                Document(
                    page_content=row["description"],
                    metadata={k: v for k, v in row.items() if k != "description"},
                )
                for row in rows
            ]

    return RateLookupRetriever()
