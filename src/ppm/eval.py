"""Gold-set eval harness (plan section 6, Phase 1: a gold set of real office
questions, scored per release).

Scores citation presence, keyword coverage and source-document accuracy per
question, and writes a JSON report under exports/evals/. With real models this
measures the plan's targets (>=80% correct citations, unsupported-claim rate
tracked); with the stub provider in literal mode it is a deterministic
plumbing check - useful in CI, not a quality claim.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

from ppm.config import REPO_ROOT, Settings, get_settings
from ppm.workflows.rag import NO_SOURCE, RagWorkflow

DEFAULT_GOLD = REPO_ROOT / "data" / "gold_questions.yaml"


def load_gold(path: str | Path | None = None) -> list[dict]:
    gold_path = Path(path) if path else DEFAULT_GOLD
    items = yaml.safe_load(gold_path.read_text(encoding="utf-8"))
    if not isinstance(items, list) or not items:
        raise ValueError(f"gold set at {gold_path} is empty or malformed")
    return items


def run_eval(
    settings: Settings | None = None,
    gold: list[dict] | None = None,
    *,
    mode: str = "auto",
    limit: int | None = None,
    out_dir: str | Path | None = None,
    save: bool = True,
) -> dict:
    settings = settings or get_settings()
    items = (gold or load_gold())
    if limit:
        items = items[:limit]
    workflow = RagWorkflow(settings)

    results: list[dict] = []
    for item in items:
        query = item.get("query") or item["question"]
        result = workflow.ask(query, mode=mode, log=False)
        answer = result.get("answer", NO_SOURCE)
        chunks = result.get("chunks") or []

        expect_no_source = bool(item.get("expect_no_source"))
        answered_with_sources = answer.strip() != NO_SOURCE and "[S" in answer
        citation_ok = (not answered_with_sources) if expect_no_source else answered_with_sources

        keywords = list(item.get("expect_keywords", []))
        keyword_hits = [keyword for keyword in keywords if keyword.lower() in answer.lower()]
        keyword_ok = (len(keyword_hits) == len(keywords)) if keywords else None

        expect_source = item.get("expect_source")
        source_ok = (
            any(expect_source.lower() in (chunk.get("source_path") or "").lower() for chunk in chunks)
            if expect_source else None
        )

        results.append({
            "question": item["question"],
            "query": query,
            "answer": answer,
            "citation_ok": citation_ok,
            "keyword_ok": keyword_ok,
            "source_ok": source_ok,
            "chunks_retrieved": len(chunks),
            "unsupported_removed": result.get("citations_removed", 0),
        })

    total = len(results)
    keyword_scored = [row for row in results if row["keyword_ok"] is not None]
    source_scored = [row for row in results if row["source_ok"] is not None]
    summary = {
        "total": total,
        "citation_rate": round(sum(1 for row in results if row["citation_ok"]) / total, 3),
        "keyword_rate": (
            round(sum(1 for row in keyword_scored if row["keyword_ok"]) / len(keyword_scored), 3)
            if keyword_scored else None
        ),
        "source_rate": (
            round(sum(1 for row in source_scored if row["source_ok"]) / len(source_scored), 3)
            if source_scored else None
        ),
        "unsupported_removed": sum(row["unsupported_removed"] for row in results),
        "mode": mode,
        "provider": settings.provider,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    out_path = None
    if save:
        directory = Path(out_dir) if out_dir else REPO_ROOT / "exports" / "evals"
        directory.mkdir(parents=True, exist_ok=True)
        out_path = directory / f"eval-{time.strftime('%Y%m%d-%H%M%S')}.json"
        out_path.write_text(
            json.dumps({"summary": summary, "results": results}, indent=2, default=str),
            encoding="utf-8",
        )

    return {"summary": summary, "results": results, "path": str(out_path) if out_path else None}
