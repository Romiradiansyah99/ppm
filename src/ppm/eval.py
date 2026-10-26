"""Evaluation harness for cited answers.

Gold questions are answered by the real Ask graph. A question counts as correct
only when the expected source file appears in the retrieved chunks and the
answer carries no uncited sentence. Refusal questions count only when the
answer is exactly "No source found."
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ppm.config import Settings, get_settings
from ppm.workflows.rag import CITATION_RE, NO_SOURCE, SENTENCE_SPLIT, AskWorkflow


def all_sentences_cited(answer: str) -> bool:
    sentences = [sentence.strip() for sentence in SENTENCE_SPLIT.split(answer) if sentence.strip()]
    return bool(sentences) and all(CITATION_RE.search(sentence) for sentence in sentences)


def load_gold(path: str | Path) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    questions = data.get("questions") or []
    if not isinstance(questions, list):
        raise ValueError("gold file must contain a 'questions' list")
    return questions


def evaluate(gold_path: str | Path, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    workflow = AskWorkflow(settings)
    items: list[dict] = []

    for item in load_gold(gold_path):
        question = str(item.get("question", "")).strip()
        if not question:
            continue
        result = workflow.ask(
            question,
            collection=item.get("collection"),
            stage=item.get("stage"),
            mode=item.get("mode", "auto"),
        )
        answer = result.get("answer") or NO_SOURCE
        chunks = result.get("chunks") or []
        expected_path = item.get("expect_path")
        expect_refusal = bool(item.get("expect_refusal"))

        if expect_refusal:
            correct = answer.strip() == NO_SOURCE
            path_hit = None
        else:
            path_hit = any(
                str(expected_path).lower() in str(chunk.get("source_path", "")).lower()
                for chunk in chunks
            ) if expected_path else True
            # The contract is about the output, not the model's first draft:
            # unsourced sentences are removed before the user sees them.
            citation_ok = answer.strip() != NO_SOURCE and all_sentences_cited(answer)
            correct = bool(path_hit and citation_ok)

        items.append({
            "question": question,
            "correct": correct,
            "expect_refusal": expect_refusal,
            "path_hit": path_hit,
            "answer": answer,
            "citations_removed": result.get("citations_removed", 0),
            "all_output_sentences_cited": all_sentences_cited(answer) if answer.strip() != NO_SOURCE else None,
            "chunks": [
                {
                    "source_path": chunk.get("source_path"),
                    "section_ref": chunk.get("section_ref"),
                    "similarity": chunk.get("similarity"),
                }
                for chunk in chunks
            ],
        })

    total = len(items)
    correct_count = sum(1 for item in items if item["correct"])
    refusal_items = [item for item in items if item["expect_refusal"]]
    answer_items = [item for item in items if not item["expect_refusal"]]
    summary = {
        "total": total,
        "correct": correct_count,
        "score": round(correct_count / total, 4) if total else 0.0,
        "answerable": len(answer_items),
        "answerable_correct": sum(1 for item in answer_items if item["correct"]),
        "refusal": len(refusal_items),
        "refusal_correct": sum(1 for item in refusal_items if item["correct"]),
        "uncited_dropped": sum(int(item["citations_removed"]) for item in items),
        "uncited_in_output": sum(
            1 for item in items
            if item["expect_refusal"] is False
            and item["all_output_sentences_cited"] is False
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "settings": {
            "chat_model": settings.chat_model,
            "embed_model": settings.embed_model,
            "top_k": settings.top_k,
            "min_similarity": settings.min_similarity,
        },
    }
    return {"summary": summary, "items": items}


def write_report(report: dict, directory: str | Path) -> Path:
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = target / f"ask-eval-{stamp}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
