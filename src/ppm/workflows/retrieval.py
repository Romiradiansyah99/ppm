"""Retrieval: deterministic template lookup plus hybrid chunk search.

SQL filters first, vector similarity second. Embeddings live on
document_chunk, so the data stays framework-neutral.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ppm import db
from ppm.config import Settings, get_settings
from ppm.models import ModelUnavailable, embed_query
from ppm.text import literal_terms

_TEMPLATE_SELECT = """
SELECT t.template_id, t.code, t.title, t.stage, t.category, t.format,
       t.source_collection, t.is_blank, t.has_sample_content, t.status,
       d.document_id, d.source_path, d.ingest_status, d.licence_class
FROM template t
LEFT JOIN document d ON d.document_id = t.document_id
WHERE t.status = 'active'
"""

_CHUNK_SELECT = """
SELECT c.chunk_id, c.chunk_index, c.section_ref, c.text_content,
       d.document_id, d.source_path, d.licence_class, d.confidentiality_class,
       d.source_collection,
       t.template_id, t.title AS template_title, t.stage AS template_stage,
       t.is_blank, t.code AS template_code
       {similarity}
FROM document_chunk c
JOIN document d ON d.document_id = c.document_id
LEFT JOIN template t ON t.document_id = d.document_id
WHERE d.ingest_status IN ('indexed', 'indexed_text_only')
  AND d.licence_class IN ('office_authored', 'permitted')
  AND (COALESCE(current_setting('ppm.access_level', true), 'baseline') = 'all'
       OR d.confidentiality_class = 'internal')
  AND length(c.text_content) >= 200
  AND strpos(c.text_content, '[Type here]') = 0
"""


@dataclass(frozen=True)
class TemplateQuery:
    text: str | None = None
    stage: str | None = None
    category: str | None = None
    format: str | None = None
    collection: str | None = None
    limit: int = 100

    def filters_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "category": self.category,
            "format": self.format,
            "collection": self.collection,
        }


@dataclass(frozen=True)
class ChunkQuery:
    text: str | None = None
    collection: str | None = None
    stage: str | None = None
    document_id: str | None = None
    limit: int = 8

    def filters_dict(self) -> dict[str, Any]:
        return {
            "collection": self.collection,
            "stage": self.stage,
            "document_id": self.document_id,
        }


def build_template_sql(query: TemplateQuery, match_all: bool = True) -> tuple[str, list[Any]]:
    """Pure SQL builder - unit tested without a database.

    Multi-term queries require every term by default (AND); callers may fall
    back to OR when that returns nothing. Results are ranked by how many terms
    appear in the title, so "risk register" beats an unrelated "register".
    """
    sql = _TEMPLATE_SELECT
    params: list[Any] = []
    terms = literal_terms(query.text) if query.text else []
    score_params: list[Any] = []
    if terms:
        clauses = []
        for term in terms:
            clauses.append("(t.title ILIKE %s OR COALESCE(t.code, '') ILIKE %s OR COALESCE(t.category, '') ILIKE %s)")
            params.extend([f"%{term}%"] * 3)
        joiner = " AND " if match_all else " OR "
        sql += " AND (" + joiner.join(clauses) + ")"
        score = " + ".join("(CASE WHEN t.title ILIKE %s THEN 1 ELSE 0 END)" for _ in terms)
        score_params = [f"%{term}%" for term in terms]
    if query.stage:
        sql += " AND t.stage = %s"
        params.append(query.stage)
    if query.category:
        sql += " AND t.category = %s"
        params.append(query.category)
    if query.format:
        sql += " AND t.format = %s"
        params.append(query.format)
    if query.collection:
        sql += " AND t.source_collection = %s"
        params.append(query.collection)

    if score_params:
        sql += " ORDER BY (" + score + ") DESC, t.stage, t.code NULLS LAST, t.title"
        params.extend(score_params)
    else:
        sql += " ORDER BY t.stage, t.code NULLS LAST, t.title"
    sql += " LIMIT %s"
    params.append(query.limit)
    return sql, params


GENERIC_TERMS = {"report", "project", "template", "document"}


def find_templates(query: TemplateQuery, *, log: bool = True) -> list[dict]:
    # AND first: precise for catalogue searches. If that is empty, retry OR but
    # require at least one non-generic term ("non-conformance report" still
    # finds "NCR Form"; "harga besi" still finds nothing).
    sql, params = build_template_sql(query, match_all=True)
    rows = db.fetch_all(sql, params)
    if not rows and query.text:
        terms = literal_terms(query.text)
        distinctive = [term for term in terms if term.lower() not in GENERIC_TERMS]
        # Fetch a wide candidate set before scoring: a LIMIT 3 here would drop
        # the distinctive match in favour of generic "report" titles.
        candidate_query = TemplateQuery(
            text=query.text,
            stage=query.stage,
            category=query.category,
            format=query.format,
            collection=query.collection,
            limit=max(query.limit, 200),
        )
        or_sql, or_params = build_template_sql(candidate_query, match_all=False)
        candidates = db.fetch_all(or_sql, or_params)
        scored: list[tuple[int, int, dict]] = []
        for row in candidates:
            title = str(row.get("title") or "").lower()
            code = str(row.get("code") or "").lower()
            category = str(row.get("category") or "").lower()
            title_hits = sum(1 for term in terms if term.lower() in title)
            distinct_hits = sum(
                1 for term in distinctive
                if term.lower() in title or term.lower() in code or term.lower() in category
            )
            if distinct_hits:
                # A title match is worth three category/code matches: "Cost
                # Plan - Template" must beat "Cost Monitoring Tool" whose
                # category happens to be cost_plan.
                scored.append((title_hits * 3 + distinct_hits, title_hits, row))
        scored.sort(key=lambda item: (-item[0], -item[1], item[2].get("title") or ""))
        rows = [row for _, _, row in scored[: query.limit]]
    for row in rows:
        row["template_id"] = str(row["template_id"])
        if row.get("document_id"):
            row["document_id"] = str(row["document_id"])
    if log:
        _log_search("find", query.text, query.filters_dict(), len(rows))
    return rows


def build_chunk_sql(query: ChunkQuery, has_vector: bool, terms: list[str]) -> tuple[str, list[Any]]:
    """Pure SQL builder for chunk search. When has_vector is true the caller
    binds the query vector as the first parameter."""
    params: list[Any] = []
    sql = _CHUNK_SELECT.format(
        similarity=(
            ", (1 - (c.embedding <=> %s::vector)) AS similarity"
            if has_vector else ", NULL AS similarity"
        )
    )
    if has_vector:
        params.append(None)  # placeholder; bound by the caller

    if terms:
        sql += " AND (" + " OR ".join("c.text_content ILIKE %s" for _ in terms) + ")"
        params.extend(f"%{term}%" for term in terms)
    if query.collection:
        sql += " AND d.source_collection = %s"
        params.append(query.collection)
    if query.stage:
        sql += " AND t.stage = %s"
        params.append(query.stage)
    if query.document_id:
        sql += " AND d.document_id = %s"
        params.append(query.document_id)
    if has_vector:
        sql += " AND c.embedding IS NOT NULL"

    if has_vector:
        sql += " ORDER BY similarity DESC"
    elif terms:
        score = " + ".join("(c.text_content ILIKE %s)::int" for _ in terms)
        params.extend(f"%{term}%" for term in terms)
        sql += f" ORDER BY {score} DESC, d.source_path, c.chunk_index"
    else:
        sql += " ORDER BY d.source_path, c.chunk_index"
    sql += " LIMIT %s"
    params.append(query.limit)
    return sql, params


def _bind_vector(params: list[Any], vector: list[float]) -> list[Any]:
    bound = list(params)
    if bound and bound[0] is None:
        bound[0] = vector
    return bound


def chunk_search(
    query: ChunkQuery,
    settings: Settings | None = None,
    *,
    mode: str = "auto",
    log: bool = True,
    access_level: str = "all",
) -> tuple[list[dict], str | None]:
    """Hybrid chunk search. mode: auto (vector then literal fallback),
    literal (no embeddings) or vector (fail loudly when unavailable)."""
    settings = settings or get_settings()
    vector: list[float] | None = None
    degraded: str | None = None

    if query.text and mode != "literal":
        try:
            vector = embed_query(settings, query.text)
        except ModelUnavailable as exc:
            if mode == "vector":
                raise
            degraded = f"vector similarity off ({exc}); matched literally instead"

    terms = literal_terms(query.text) if (query.text and vector is None) else []
    sql, params = build_chunk_sql(query, has_vector=vector is not None, terms=terms)
    if vector is not None:
        params = _bind_vector(params, vector)

    rows = db.fetch_all_access(access_level, sql, params)
    for row in rows:
        row["chunk_id"] = str(row["chunk_id"])
        row["document_id"] = str(row["document_id"])
        if row.get("template_id"):
            row["template_id"] = str(row["template_id"])
        if row.get("similarity") is not None:
            row["similarity"] = float(row["similarity"])

    if log:
        _log_search("ask", query.text, query.filters_dict(), len(rows))
    return rows, degraded


def _log_search(kind: str, text: str | None, filters: dict, count: int) -> None:
    try:
        user = get_settings().user_label
        db.execute(
            "INSERT INTO search_log (user_label, kind, query, filters, result_count) "
            "VALUES (%s, %s, %s, %s, %s)",
            (user, kind, text, json.dumps(filters), count),
        )
    except Exception:
        pass  # instrumentation must never break a search
