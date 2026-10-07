"""Cited RAG over the document index (plan section 6, Phase 1).

retrieve -> grade relevance -> generate with inline [S#] citations ->
citation-check: any sentence without a supporting retrieved chunk is replaced
with "No source found." - unsourced claims are never paraphrased.

Runs without a checkpointer (read-only, no interrupts); the guardrail node
that re-checks chunk permissions arrives with the Phase 3 silos.
"""

from __future__ import annotations

import json
import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ppm.config import Settings, get_settings
from ppm.models import ModelUnavailable, get_answer_generator
from ppm.workflows.retrieval import chunk_search

NO_SOURCE = "No source found."
MAX_CONTEXT_CHUNKS = 5

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_CITATION_RE = re.compile(r"\[S(\d+)\]")


class RagState(TypedDict, total=False):
    question: str
    person_id: str | None
    document_id: str | None
    mode: str                      # auto | literal | vector
    chunks: list[dict]
    weak_context: bool
    answer_raw: str
    answer: str
    citations_removed: int
    degraded: str | None
    stats: dict


def check_citations(answer: str, chunk_count: int) -> tuple[str, int]:
    """Pure function: every sentence must carry a valid [S#]; else replaced."""
    kept: list[str] = []
    removed = 0
    for sentence in _SENTENCE_SPLIT.split(answer):
        sentence = sentence.strip()
        if not sentence:
            continue
        refs = [int(m) for m in _CITATION_RE.findall(sentence)]
        if any(1 <= ref <= chunk_count for ref in refs):
            kept.append(sentence)
        else:
            removed += 1
            kept.append(NO_SOURCE)
    return " ".join(kept), removed


class RagWorkflow:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._app = None

    # --- nodes -----------------------------------------------------------------

    def _retrieve(self, state: RagState) -> dict:
        chunks, degraded = chunk_search(
            state["question"],
            self.settings,
            document_id=state.get("document_id"),
            limit=10,
            mode=state.get("mode", "auto"),
        )
        return {"chunks": chunks, "degraded": degraded}

    def _grade(self, state: RagState) -> dict:
        chunks = state.get("chunks") or []
        graded = chunks[:MAX_CONTEXT_CHUNKS]
        weak = bool(graded) and graded[0].get("similarity") is not None and graded[0]["similarity"] < 0.25
        return {"chunks": graded, "weak_context": weak}

    def _generate(self, state: RagState) -> dict:
        chunks = state.get("chunks") or []
        try:
            generator = get_answer_generator(self.settings)
            answer = generator.answer(state["question"], chunks)
        except ModelUnavailable as exc:
            answer = NO_SOURCE
            return {"answer_raw": answer, "answer": answer, "degraded": str(exc)}
        return {"answer_raw": answer}

    def _citation_check(self, state: RagState) -> dict:
        chunks = state.get("chunks") or []
        answer_raw = state.get("answer_raw", NO_SOURCE)
        if not chunks or answer_raw.strip() == NO_SOURCE:
            stats = dict(state.get("stats") or {})
            stats.update({"chunks_retrieved": len(chunks), "citations_removed": 0,
                          "weak_context": bool(state.get("weak_context"))})
            return {"answer": NO_SOURCE, "citations_removed": 0, "stats": stats}
        checked, removed = check_citations(answer_raw, len(chunks))
        stats = dict(state.get("stats") or {})
        stats.update({
            "chunks_retrieved": len(chunks),
            "citations_removed": removed,
            "weak_context": bool(state.get("weak_context")),
        })
        return {"answer": checked, "citations_removed": removed, "stats": stats}

    # --- assembly ----------------------------------------------------------------

    def compile(self, checkpointer=None):
        graph = StateGraph(RagState)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("grade", self._grade)
        graph.add_node("generate", self._generate)
        graph.add_node("citation_check", self._citation_check)
        graph.add_edge(START, "retrieve")
        graph.add_edge("retrieve", "grade")
        graph.add_edge("grade", "generate")
        graph.add_edge("generate", "citation_check")
        graph.add_edge("citation_check", END)
        self._app = graph.compile(checkpointer=checkpointer)
        return self._app

    def ask(self, question: str, *, document_id: str | None = None, mode: str = "auto",
            person_id: str | None = None, log: bool = True) -> dict[str, Any]:
        if self._app is None:
            self.compile()
        state: RagState = {
            "question": question,
            "document_id": document_id,
            "mode": mode,
            "person_id": person_id,
            "stats": {},
        }
        result = self._app.invoke(state)
        if log:
            self._log(question, mode, result)
        return result

    def _log(self, question: str, mode: str, result: dict) -> None:
        try:
            from ppm import db

            db.execute(
                "INSERT INTO search_log (user_label, query, filters, result_count) VALUES (%s, %s, %s, %s)",
                (self.settings.user_label, question, json.dumps({"kind": "rag", "mode": mode}),
                 len(result.get("chunks") or [])),
            )
        except Exception:
            pass  # instrumentation must never break a search
