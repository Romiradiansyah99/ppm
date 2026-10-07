"""Small text utilities shared by the stub generator and the degraded
(literal-match) retrieval path: significant-term extraction with a minimal
English stopword list."""

from __future__ import annotations

import re

STOPWORDS = {
    "what", "when", "where", "which", "with", "this", "that", "have", "has", "had",
    "used", "using", "from", "into", "about", "much", "many", "does", "doing",
    "cost", "costs", "rate", "rates", "there", "then", "they", "will", "would",
    "the", "and", "for", "are", "was", "were", "but", "not", "you", "all", "any",
    "can", "use", "how", "who", "why", "its", "our", "out", "own", "see", "per",
    "via", "one",
}


def literal_terms(text: str, max_terms: int = 6) -> list[str]:
    """Significant terms of a natural-language query, in order of appearance."""
    terms: list[str] = []
    for word in re.findall(r"[A-Za-z0-9']+", text.lower()):
        if len(word) >= 3 and word not in STOPWORDS and word not in terms:
            terms.append(word)
    return terms[:max_terms]
