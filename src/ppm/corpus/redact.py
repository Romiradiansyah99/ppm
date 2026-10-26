"""Client-name redaction.

Scanned against extracted text, never only against file names. Every hit is
recorded so the ingest review gate can ask a human before the text is embedded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PLACEHOLDER = "[CLIENT]"


@dataclass(frozen=True)
class Hit:
    term: str
    count: int


def load_blocklist(path: str | Path) -> list[str]:
    blocklist_path = Path(path)
    if not blocklist_path.is_file():
        return []
    terms: list[str] = []
    for line in blocklist_path.read_text(encoding="utf-8").splitlines():
        term = line.strip()
        if not term or term.startswith("#"):
            continue
        terms.append(term)
    return terms


def _pattern(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", re.IGNORECASE)


def scan_text(text: str, terms: list[str]) -> list[Hit]:
    hits: list[Hit] = []
    for term in terms:
        count = len(_pattern(term).findall(text))
        if count:
            hits.append(Hit(term=term, count=count))
    return hits


def redact_text(text: str, terms: list[str]) -> tuple[str, list[Hit]]:
    hits: list[Hit] = []
    redacted = text
    for term in terms:
        pattern = _pattern(term)
        count = len(pattern.findall(redacted))
        if count:
            redacted = pattern.sub(PLACEHOLDER, redacted)
            hits.append(Hit(term=term, count=count))
    return redacted, hits


def merge_hits(hits: list[Hit]) -> list[dict[str, int | str]]:
    merged: dict[str, int] = {}
    for hit in hits:
        merged[hit.term] = merged.get(hit.term, 0) + hit.count
    return [{"term": term, "count": count} for term, count in sorted(merged.items())]
