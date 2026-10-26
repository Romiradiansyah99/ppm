"""Cited RAG over the document index.

retrieve -> grade -> refuse (when nothing useful was found)
                 -> generate -> citation_check -> finalise

A sentence without a valid [S#] citation is replaced with "No source found.".
Unsourced claims are never paraphrased into an answer.

This is a linear Python flow, not a LangGraph workflow.
"""

from __future__ import annotations

import re
from typing import Any

from ppm.config import Settings, get_settings
from ppm.models import ModelUnavailable, answer_question
from ppm.workflows.retrieval import ChunkQuery, TemplateQuery, chunk_search, find_templates

NO_SOURCE = "No source found."
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
CITATION_RE = re.compile(r"\[S(\d+)\]")


def check_citations(answer: str, chunk_count: int) -> tuple[str, int]:
    """Drop every sentence without a valid [S#]. If nothing survives, refuse.

    Uncited claims are never paraphrased into the answer; they are removed.
    """
    kept: list[str] = []
    removed = 0
    for sentence in SENTENCE_SPLIT.split(answer):
        sentence = sentence.strip()
        if not sentence:
            continue
        refs = [int(match) for match in CITATION_RE.findall(sentence)]
        if any(1 <= ref <= chunk_count for ref in refs):
            kept.append(sentence)
        else:
            removed += 1
    return (" ".join(kept) if kept else NO_SOURCE), removed


def is_lookup_question(question: str) -> bool:
    lowered = question.lower()
    markers = ("template", "dimana", "di mana", "where", "which", "file", "lokasi", "letak")
    return any(marker in lowered for marker in markers)


class AskWorkflow:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _retrieve(self, state: dict) -> dict:
        # The catalog is a first-class source: "which template for X" is a
        # metadata question, not a text-similarity question.
        try:
            catalog = find_templates(
                TemplateQuery(
                    text=state["question"],
                    stage=state.get("stage"),
                    collection=state.get("collection"),
                    limit=3,
                ),
                log=False,
            )
        except Exception:
            # Chunk retrieval may still work; a catalog lookup failure must not
            # turn a cited-answer request into a database traceback.
            catalog = []
        catalog_chunks = [
            {
                "chunk_id": f"catalog:{row['template_id']}",
                "section_ref": "catalog",
                "text_content": (
                    f"Template {row.get('code') or '-'} - {row['title']}; "
                    f"stage {row.get('stage')}; category {row.get('category') or 'other'}; "
                    f"format {row.get('format')}; blank template "
                    f"{'yes' if row.get('is_blank') else 'no'}"
                ),
                "source_path": row.get("source_path"),
                "source_collection": row.get("source_collection"),
                "template_title": row["title"],
                "is_blank": row.get("is_blank"),
                "similarity": None,
            }
            for row in catalog
        ]

        chunks, degraded = chunk_search(
            ChunkQuery(
                text=state["question"],
                collection=state.get("collection"),
                stage=state.get("stage"),
                document_id=state.get("document_id"),
                limit=10,
            ),
            self.settings,
            mode=state.get("mode", "auto"),
            access_level=state.get("access_level", "all"),
        )
        return {"chunks": catalog_chunks + chunks, "degraded": degraded}

    def _grade(self, state: dict) -> dict:
        chunks = state.get("chunks") or []
        catalog = [chunk for chunk in chunks if chunk.get("section_ref") == "catalog"][:3]
        text_chunks = [chunk for chunk in chunks if chunk.get("section_ref") != "catalog"][: self.settings.top_k]
        graded = catalog + text_chunks
        similarities = [
            chunk.get("similarity") for chunk in text_chunks
            if chunk.get("similarity") is not None
        ]
        top = max(similarities) if similarities else None
        weak = False if catalog else (
            not graded or (top is not None and top < self.settings.min_similarity)
        )
        return {
            "chunks": graded,
            "weak_context": weak,
            "catalog_lookup": bool(catalog) and is_lookup_question(state["question"]),
        }

    def _refuse(self, state: dict) -> dict:
        stats = dict(state.get("stats") or {})
        stats.update({
            "chunks_retrieved": len(state.get("chunks") or []),
            "refused": True,
            "reason": "no_context" if not state.get("chunks") else "weak_context",
        })
        return {"answer": NO_SOURCE, "answer_raw": NO_SOURCE, "citations_removed": 0, "stats": stats}

    def _catalog_answer(self, state: dict) -> dict:
        """Deterministic answer for "which template" questions.

        The catalog already contains the answer; asking a small model to
        restate a file path adds latency and failure modes.
        """
        catalog = [chunk for chunk in state.get("chunks") or [] if chunk.get("section_ref") == "catalog"]
        parts = []
        for index, chunk in enumerate(catalog, start=1):
            sentence = str(chunk.get("text_content", "")).strip().rstrip(".")
            parts.append(f"{sentence} [S{index}].")
        answer = " ".join(parts) or NO_SOURCE
        stats = dict(state.get("stats") or {})
        stats.update({"chunks_retrieved": len(state.get("chunks") or []), "catalog_answer": True})
        return {"answer": answer, "answer_raw": answer, "citations_removed": 0, "stats": stats}

    def _generate(self, state: dict) -> dict:
        try:
            answer = answer_question(self.settings, state["question"], state.get("chunks") or [])
        except ModelUnavailable as exc:
            return {"answer_raw": NO_SOURCE, "degraded": str(exc)}
        return {"answer_raw": answer}

    def _citation_check(self, state: dict) -> dict:
        chunks = state.get("chunks") or []
        answer_raw = state.get("answer_raw") or NO_SOURCE
        refused_by_model = answer_raw.strip().lower().rstrip(".") == NO_SOURCE.lower().rstrip(".")
        if not chunks or refused_by_model:
            checked, removed = NO_SOURCE, 0
        else:
            checked, removed = check_citations(answer_raw, len(chunks))
        stats = dict(state.get("stats") or {})
        stats.update({
            "chunks_retrieved": len(chunks),
            "citations_removed": removed,
            "weak_context": bool(state.get("weak_context")),
            "catalog_answer": bool(state.get("catalog_lookup")),
            "refused": False,
        })
        return {"answer": checked, "citations_removed": removed, "stats": stats}

    def ask(
        self,
        question: str,
        *,
        collection: str | None = None,
        stage: str | None = None,
        document_id: str | None = None,
        mode: str = "auto",
        access_level: str = "all",
    ) -> dict[str, Any]:
        state: dict[str, Any] = {
            "question": question,
            "collection": collection,
            "stage": stage,
            "document_id": document_id,
            "mode": mode,
            "access_level": access_level,
            "stats": {},
        }
        state.update(self._retrieve(state))
        state.update(self._grade(state))
        if state.get("weak_context"):
            state.update(self._refuse(state))
            return state
        if state.get("catalog_lookup"):
            state.update(self._catalog_answer(state))
        else:
            state.update(self._generate(state))
        state.update(self._citation_check(state))
        return state
