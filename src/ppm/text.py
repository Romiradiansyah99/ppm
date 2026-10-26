"""Small text helpers shared by retrieval and evaluation."""

from __future__ import annotations

import re

_STOPWORDS = {
    "yang", "dan", "untuk", "dengan", "dari", "pada", "dalam", "adalah", "atau",
    "apa", "itu", "ini", "saya", "kita", "the", "and", "for", "with", "from",
    "what", "which", "that", "this", "how", "are", "was", "were", "does", "did",
    "template", "dipakai", "digunakan", "dokumen", "document", "file", "apakah",
    "berapa", "per", "hari", "pada", "dalam", "dengan", "atau",
}

_TERM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")

_SYNONYMS = {
    "non-conformance": ["ncr"],
    "nonconformance": ["ncr"],
    "nonconformity": ["ncr"],
    "ncr": ["non-conformance"],
}


def literal_terms(text: str, limit: int = 6) -> list[str]:
    """Significant terms for a literal fallback search (no embeddings)."""
    terms: list[str] = []
    for match in _TERM_RE.findall(text or ""):
        term = match.strip("._/-")
        if len(term) < 3 or term.lower() in _STOPWORDS:
            continue
        if term.lower() not in {item.lower() for item in terms}:
            terms.append(term)
        for synonym in _SYNONYMS.get(term.lower(), []):
            if synonym.lower() not in {item.lower() for item in terms}:
                terms.append(synonym)
    return terms[:limit]
