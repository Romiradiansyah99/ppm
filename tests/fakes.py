"""Deterministic model doubles for tests. Never available at runtime."""

from __future__ import annotations

import hashlib
import math
from types import SimpleNamespace


class HashEmbedder:
    """Deterministic pseudo-vectors: proves the plumbing, not the similarity."""

    def __init__(self, dim: int = 768) -> None:
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        values: list[float] = []
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        while len(values) < self.dim:
            digest = hashlib.sha256(digest).digest()
            values.extend(byte / 127.5 - 1.0 for byte in digest)
        vector = values[: self.dim]
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class FakeChat:
    def __init__(self, answer: str = "No source found.") -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def invoke(self, prompt: str):
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.answer)
