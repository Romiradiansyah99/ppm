"""Deterministic ingest state machine.

classify -> load (hash, dedupe) -> redact -> gate -> [staged review] ->
chunk -> persist (document + template + chunks).

No LangGraph, no checkpoint, no interrupt. A review stop is a normal row in
`ingest_staging`; resume applies the human decision and continues.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from ppm import db
from ppm.config import Settings, get_settings
from ppm.corpus.manifest import CorpusEntry, scan_file_hash
from ppm.corpus.redact import load_blocklist
from ppm.loaders import ParsedDocument, document_stats, load_document
from ppm.models import ModelUnavailable, embed_texts
from ppm.workflows.chunking import chunk_document

ALLOWED_LICENCES = {"office_authored", "permitted"}
MIME_BY_SUFFIX = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
}


def _update_run(
    thread_id: str,
    status: str,
    *,
    document_id: str | None = None,
    stats: dict | None = None,
    error: str | None = None,
    create: bool = False,
    source_path: str | None = None,
) -> None:
    if create:
        db.execute(
            "INSERT INTO ingest_run (thread_id, document_id, source_path, status, stats, error) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (thread_id) DO UPDATE SET "
            "status = EXCLUDED.status, stats = EXCLUDED.stats, error = EXCLUDED.error, updated_at = now()",
            (thread_id, document_id, source_path, status, json.dumps(stats or {}), error),
        )
        return
    db.execute(
        "UPDATE ingest_run SET status = %s, stats = COALESCE(%s, stats), error = %s, "
        "document_id = COALESCE(%s, document_id), updated_at = now() WHERE thread_id = %s",
        (status, json.dumps(stats) if stats is not None else None, error, document_id, thread_id),
    )


def _stage(state: dict, review: dict) -> None:
    db.execute(
        "INSERT INTO ingest_staging (thread_id, entry, parsed, redacted, stats, review) "
        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (thread_id) DO UPDATE SET "
        "entry = EXCLUDED.entry, parsed = EXCLUDED.parsed, redacted = EXCLUDED.redacted, "
        "stats = EXCLUDED.stats, review = EXCLUDED.review, updated_at = now()",
        (
            state["thread_id"],
            json.dumps(state["entry"]),
            json.dumps(state.get("parsed")),
            json.dumps(state.get("redacted")),
            json.dumps(state.get("stats") or {}),
            json.dumps(review),
        ),
    )


def _staging(thread_id: str) -> dict[str, Any] | None:
    return db.fetch_one("SELECT * FROM ingest_staging WHERE thread_id = %s", (thread_id,))


class IngestWorkflow:
    def __init__(self, settings: Settings | None = None, checkpointer=None) -> None:
        # checkpointer is accepted only for older call sites; it is ignored.
        self.settings = settings or get_settings()
        self.checkpointer = checkpointer
        self.blocklist_path = self.settings.manifest_path.parent / "client_blocklist.txt"

    # --- steps -----------------------------------------------------------------

    def _classify(self, state: dict) -> dict:
        entry = CorpusEntry.from_state(state["entry"])
        _update_run(state["thread_id"], "running", create=True, source_path=str(entry.path))
        return {
            "licence_class": entry.licence_class,
            "confidentiality_class": "internal",
            "index": entry.index,
            "status": "running",
            "error": None,
            "stats": {
                "collection": entry.collection,
                "format": entry.format,
                "code": entry.code,
                "title": entry.title,
                "is_template": entry.is_template,
            },
        }

    def _load(self, state: dict) -> dict:
        entry = CorpusEntry.from_state(state["entry"])
        try:
            file_hash = scan_file_hash(entry.path)
        except OSError as exc:
            return self._fail(state, f"cannot read file: {exc}")

        existing = db.fetch_one(
            "SELECT document_id, ingest_status FROM document WHERE hash = %s",
            (file_hash,),
        )
        if existing:
            db.execute(
                "INSERT INTO document_alias (document_id, source_path, source_collection) "
                "VALUES (%s, %s, %s) ON CONFLICT (source_path) DO NOTHING",
                (existing["document_id"], str(entry.path), entry.collection),
            )
            _update_run(state["thread_id"], "duplicate", document_id=str(existing["document_id"]))
            return {
                "status": "duplicate",
                "document_id": str(existing["document_id"]),
                "hash": file_hash,
                "stats": {**dict(state.get("stats") or {}), "duplicate_of": str(existing["document_id"])},
            }

        mime = MIME_BY_SUFFIX.get(Path(entry.path).suffix.lower(), "application/octet-stream")
        row = db.fetch_one(
            "INSERT INTO document (hash, source_path, canonical_path, source_collection, mime, "
            "licence_class, confidentiality_class, ingest_status, extractor) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s) RETURNING document_id",
            (
                file_hash, str(entry.path), str(entry.path), entry.collection, mime,
                entry.licence_class, state.get("confidentiality_class", "internal"), entry.format,
            ),
        )
        document_id = str(row["document_id"])

        if not entry.index:
            return {
                "document_id": document_id,
                "hash": file_hash,
                "status": "reference_only",
                "stats": {**dict(state.get("stats") or {}), "reason": "not_indexable_by_manifest"},
            }

        try:
            parsed = load_document(entry.path)
        except Exception as exc:
            return self._fail(state, f"parse failed: {exc}", document_id=document_id)

        stats = dict(state.get("stats") or {})
        stats["parse"] = document_stats(parsed)
        stats["hash"] = file_hash
        review_reasons: list[str] = []
        if parsed.is_empty and entry.format == "pdf":
            review_reasons.append("no_text_layer")
        stats["review_reasons"] = review_reasons

        return {
            "document_id": document_id,
            "hash": file_hash,
            "parsed": parsed.to_state(),
            "stats": stats,
        }

    def _redact(self, state: dict) -> dict:
        if not state.get("parsed"):
            return {}
        parsed = ParsedDocument.from_state(state["parsed"])
        terms = load_blocklist(self.blocklist_path)
        redacted, hits = parsed.redact(terms)

        stats = dict(state.get("stats") or {})
        reasons = list(stats.get("review_reasons") or [])
        if hits:
            reasons.append("client_name_detected")
        if state.get("licence_class") not in ALLOWED_LICENCES:
            reasons.append("licence_not_indexable")
        stats["review_reasons"] = sorted(set(reasons))

        db.execute(
            "UPDATE document SET redaction_hits = %s, updated_at = now() WHERE document_id = %s",
            (json.dumps(hits), state["document_id"]),
        )
        return {"redacted": redacted.to_state(), "redaction_hits": hits, "stats": stats}

    def _review_payload(self, state: dict) -> dict:
        entry = CorpusEntry.from_state(state["entry"])
        return {
            "kind": "ingest_review",
            "thread_id": state["thread_id"],
            "document_id": state.get("document_id"),
            "source_path": str(entry.path),
            "title": entry.title,
            "collection": entry.collection,
            "reasons": (state.get("stats") or {}).get("review_reasons") or [],
            "redaction_hits": state.get("redaction_hits") or [],
            "parse": (state.get("stats") or {}).get("parse") or {},
            "proposed": {
                "licence_class": state.get("licence_class"),
                "confidentiality_class": state.get("confidentiality_class"),
            },
            "resume_with": {
                "action": "approve_redacted | reference_only | forbid",
                "licence_class": "office_authored | permitted | restricted | forbidden",
                "confidentiality_class": "internal | client_identifiable | client_confidential",
            },
        }

    def _chunk(self, state: dict) -> dict:
        if not state.get("redacted"):
            return {"chunks": []}
        parsed = ParsedDocument.from_state(state["redacted"])
        drafts = chunk_document(parsed)
        stats = dict(state.get("stats") or {})
        stats["chunks_planned"] = len(drafts)
        return {"chunks": [draft.to_state() for draft in drafts], "stats": stats}

    def _persist(self, state: dict) -> dict:
        entry = CorpusEntry.from_state(state["entry"])
        document_id = state["document_id"]
        chunks = state.get("chunks") or []

        vectors: list[list[float] | None] = [None] * len(chunks)
        warning: str | None = None
        try:
            vectors = list(embed_texts(self.settings, [chunk["text_content"] for chunk in chunks]))
        except ModelUnavailable as exc:
            warning = f"embeddings skipped: {exc}"

        embedded = any(vector is not None for vector in vectors)
        status = "indexed" if embedded else "indexed_text_only"
        stats = dict(state.get("stats") or {})
        stats["chunks_written"] = len(chunks)
        if warning:
            stats["embedding_warning"] = warning

        try:
            with db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE document SET ingest_status = %s, licence_class = %s, "
                    "confidentiality_class = %s, redaction_hits = %s, stats = %s, updated_at = now() "
                    "WHERE document_id = %s",
                    (
                        status,
                        state.get("licence_class"),
                        state.get("confidentiality_class"),
                        json.dumps(state.get("redaction_hits") or []),
                        json.dumps(stats),
                        document_id,
                    ),
                )
                cur.execute("DELETE FROM document_chunk WHERE document_id = %s", (document_id,))
                for index, chunk in enumerate(chunks):
                    cur.execute(
                        "INSERT INTO document_chunk (document_id, chunk_index, section_ref, text_content, "
                        "token_estimate, embedding) VALUES (%s, %s, %s, %s, %s, %s)",
                        (
                            document_id, index, chunk["section_ref"], chunk["text_content"],
                            chunk["token_estimate"], vectors[index] if index < len(vectors) else None,
                        ),
                    )
                self._upsert_template(cur, entry, document_id, state.get("hash") or "")
                cur.execute(
                    "UPDATE ingest_run SET status = 'completed', document_id = %s, stats = %s, "
                    "updated_at = now() WHERE thread_id = %s",
                    (document_id, json.dumps(stats), state["thread_id"]),
                )
        except Exception as exc:
            return self._fail(state, f"write failed: {exc}")

        return {"status": status, "stats": stats, "error": None}

    def _finish_reference(self, state: dict) -> dict:
        entry = CorpusEntry.from_state(state["entry"])
        status = "forbidden" if (state.get("review") or {}).get("action") == "forbid" else "reference_only"
        stats = dict(state.get("stats") or {})
        stats["reason"] = stats.get("reason") or (state.get("review") or {}).get("action") or "not_indexable"
        try:
            with db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE document SET ingest_status = %s, licence_class = %s, "
                    "confidentiality_class = %s, redaction_hits = %s, stats = %s, updated_at = now() "
                    "WHERE document_id = %s",
                    (
                        status,
                        state.get("licence_class"),
                        state.get("confidentiality_class"),
                        json.dumps(state.get("redaction_hits") or []),
                        json.dumps(stats),
                        state["document_id"],
                    ),
                )
                self._upsert_template(cur, entry, state["document_id"], state.get("hash") or "")
                cur.execute(
                    "UPDATE ingest_run SET status = 'completed', document_id = %s, stats = %s, "
                    "updated_at = now() WHERE thread_id = %s",
                    (state["document_id"], json.dumps(stats), state["thread_id"]),
                )
        except Exception as exc:
            return self._fail(state, f"write failed: {exc}")
        return {"status": status, "stats": stats}

    def _fail(self, state: dict, message: str, *, document_id: str | None = None) -> dict:
        _update_run(
            state["thread_id"], "failed",
            document_id=document_id or state.get("document_id"),
            error=message,
        )
        return {"status": "failed", "error": message}

    # --- template upsert --------------------------------------------------------

    @staticmethod
    def _upsert_template(cur, entry: CorpusEntry, document_id: str, file_hash: str) -> None:
        if not entry.is_template:
            return
        if not entry.code:
            cur.execute(
                "INSERT INTO template (document_id, source_collection, code, title, stage, category, "
                "format, is_blank, has_sample_content) VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s)",
                (
                    document_id, entry.collection, entry.title, entry.stage, entry.category,
                    entry.format, entry.is_blank, entry.has_sample_content,
                ),
            )
            return

        cur.execute(
            "SELECT template_id, document_id FROM template "
            "WHERE source_collection = %s AND code = %s AND variant_of IS NULL",
            (entry.collection, entry.code),
        )
        existing = cur.fetchone()
        if not existing:
            cur.execute(
                "INSERT INTO template (document_id, source_collection, code, title, stage, category, "
                "format, is_blank, has_sample_content) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    document_id, entry.collection, entry.code, entry.title, entry.stage,
                    entry.category, entry.format, entry.is_blank, entry.has_sample_content,
                ),
            )
            return

        canonical_id, canonical_document_id = existing
        canonical_hash = None
        if canonical_document_id:
            cur.execute("SELECT hash FROM document WHERE document_id = %s", (canonical_document_id,))
            row = cur.fetchone()
            canonical_hash = row[0] if row else None

        if canonical_hash and canonical_hash != file_hash:
            cur.execute(
                "INSERT INTO template (document_id, source_collection, code, title, stage, category, "
                "format, is_blank, has_sample_content, variant_of) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    document_id, entry.collection, entry.code, entry.title, entry.stage,
                    entry.category, entry.format, entry.is_blank, entry.has_sample_content,
                    canonical_id,
                ),
            )
            return

        cur.execute(
            "UPDATE template SET document_id = %s, title = %s, stage = %s, category = %s, "
            "format = %s, is_blank = %s, has_sample_content = %s WHERE template_id = %s",
            (
                document_id, entry.title, entry.stage, entry.category,
                entry.format, entry.is_blank, entry.has_sample_content, canonical_id,
            ),
        )

    # --- public API -------------------------------------------------------------

    def start(self, entry: CorpusEntry) -> dict[str, Any]:
        thread_id = str(uuid4())
        state: dict[str, Any] = {
            "thread_id": thread_id,
            "entry": entry.to_state(),
            "stats": {},
        }
        state.update(self._classify(state))
        state.update(self._load(state))

        if state.get("status") == "failed":
            return state
        if state.get("status") == "duplicate":
            return state
        if state.get("status") == "reference_only":
            state.update(self._finish_reference(state))
            state["thread_id"] = thread_id
            return state

        state.update(self._redact(state))
        reasons = (state.get("stats") or {}).get("review_reasons") or []
        if reasons:
            review = self._review_payload(state)
            state["review"] = review
            state["status"] = "awaiting_review"
            state["stats"] = {**dict(state.get("stats") or {}), "review": review}
            _stage(state, review)
            _update_run(
                thread_id, "awaiting_review",
                document_id=state.get("document_id"),
                stats=state.get("stats"),
            )
            return state

        state.update(self._chunk(state))
        state.update(self._persist(state))
        state["thread_id"] = thread_id
        return state

    def run(self, entry: CorpusEntry) -> dict[str, Any]:
        """Compatibility alias for the old graph entry point."""
        return self.start(entry)

    def resume(self, thread_id: str, decision: dict) -> dict[str, Any]:
        run = db.fetch_one("SELECT * FROM ingest_run WHERE thread_id = %s", (thread_id,))
        if not run:
            return {"thread_id": thread_id, "status": "failed", "error": "ingest thread not found"}

        staging = _staging(thread_id)
        if not staging:
            return {
                "thread_id": thread_id,
                "status": run["status"],
                "error": "no staged review for this thread",
            }

        action = str(decision.get("action", "reference_only"))
        if action not in {"approve_redacted", "reference_only", "forbid"}:
            action = "reference_only"

        proposed = (staging.get("review") or {}).get("proposed") or {}
        state: dict[str, Any] = {
            "thread_id": thread_id,
            "entry": staging["entry"],
            "parsed": staging.get("parsed"),
            "redacted": staging.get("redacted"),
            "stats": dict(staging.get("stats") or {}),
            "review": decision,
            "document_id": str(run["document_id"]) if run.get("document_id") else None,
            "hash": (staging.get("stats") or {}).get("hash"),
            "licence_class": decision.get("licence_class") or proposed.get("licence_class") or "reference_only",
            "confidentiality_class": decision.get("confidentiality_class") or proposed.get("confidentiality_class") or "internal",
            "redaction_hits": ((staging.get("review") or {}).get("redaction_hits") or []),
        }
        if action == "approve_redacted" and state["licence_class"] not in ALLOWED_LICENCES:
            action = "reference_only"
        if action != "approve_redacted":
            state.update(self._finish_reference(state))
        else:
            state.update(self._chunk(state))
            state.update(self._persist(state))

        if state.get("status") != "failed":
            db.execute("DELETE FROM ingest_staging WHERE thread_id = %s", (thread_id,))
        state["thread_id"] = thread_id
        return state
