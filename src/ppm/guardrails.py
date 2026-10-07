"""Guardrail node (plan section 6, Phase 3): defence in depth.

Every retrieved row is re-checked against confidentiality + project membership
before it reaches a model context or a UI table. This check runs on the admin
connection with an explicit query - it does not trust the RLS policies, and
the RLS policies do not trust it. A bug in either layer alone must not leak a
silo.
"""

from __future__ import annotations

from ppm import db

_VISIBLE_DOCUMENTS_SQL = """
SELECT d.document_id
FROM document d
WHERE d.document_id = ANY(%s)
  AND (
      d.confidentiality_class = 'internal'
      OR (d.project_id IS NOT NULL AND EXISTS (
          SELECT 1 FROM project_member m
          WHERE m.project_id = d.project_id AND m.person_id = %s
      ))
  )
"""


def visible_document_ids(person_id: str, document_ids: list[str]) -> set[str]:
    ids = [str(i) for i in {str(d) for d in document_ids}]
    if not ids:
        return set()
    rows = db.fetch_all(_VISIBLE_DOCUMENTS_SQL, (ids, str(person_id)))
    return {str(row["document_id"]) for row in rows}


def filter_visible(person_id: str | None, rows: list[dict], *, document_key: str = "document_id") -> list[dict]:
    """Drop rows whose document is not visible to the person. person_id=None
    means no person context: only internal documents pass."""
    if person_id is None:
        allowed = visible_document_ids_public_only(rows, document_key)
    else:
        allowed = visible_document_ids(person_id, [row[document_key] for row in rows if row.get(document_key)])
    return [row for row in rows if str(row.get(document_key)) in allowed]


def visible_document_ids_public_only(rows: list[dict], document_key: str) -> set[str]:
    ids = [str(i) for i in {str(row[document_key]) for row in rows if row.get(document_key)}]
    if not ids:
        return set()
    visible = db.fetch_all(
        "SELECT document_id FROM document WHERE document_id = ANY(%s) AND confidentiality_class = 'internal'",
        (ids,),
    )
    return {str(row["document_id"]) for row in visible}
