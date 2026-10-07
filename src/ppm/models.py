"""The single internal module for model access (plan section 6 and section 9).

Every LLM/embedding call goes through here. The residency flag gates what is
allowed: PPM_RESIDENCY=local -> Ollama on office hardware only; =api ->
approved endpoint only. `stub` is a deterministic dev-only provider used to
prove the pipeline wiring on synthetic data - never for real documents.

Only this module (and ppm.workflows / ppm.loaders) may import langchain.
Callers receive plain protocols and plain lists.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Protocol
from urllib.error import URLError
from urllib.request import urlopen

from ppm.config import Settings
from ppm.loaders import ParsedDocument
from ppm.registry import Registry
from ppm.schemas import CostPlanExtraction, ElementExtraction
from ppm.normalise import parse_date, parse_number


class ModelUnavailable(Exception):
    """A configured model endpoint or model is not reachable."""


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

class Extractor(Protocol):
    def extract(self, parsed: ParsedDocument, registry: Registry) -> CostPlanExtraction: ...


_RULES = """You are the extraction step of an office cost-plan ingest pipeline.

Extract the cost plan described in the document below into the structured schema.

Rules:
1. Emit only rows that exist in the source. Never invent, merge or average rows.
2. Strip client identity: no client names, no client contact names, in any field.
   Say "office project" instead. Project names must be internal references.
3. Every element needs a source_ref ("<sheet>!R<row>") pointing at its row.
4. confidence is 0..1 per element: 0.9+ only when every cell you emitted was
   read unambiguously. Use lower values for messy or merged cells.
5. When a definition below is UNRESOLVED, prefer null over guessing.
6. If the plan date is absent or unreadable, leave plan_date null.
7. Skip subtotal/total rows; note them in warnings instead.
"""


def _registry_context(registry: Registry) -> str:
    lines = [
        f"cost plan stages: {registry.enum_values('cost_plan', 'stage')}",
        f"area bases: {registry.area_bases()}",
        f"rate bases: {registry.rate_bases()}",
        f"sector values: {registry.enum_values('project', 'sector')}",
        f"spec levels: {registry.spec_levels()}",
        "canonical units: " + ", ".join(sorted(registry.entity('benchmark_rate').get('units', {}).get('canonical', {}).keys())),
    ]
    unresolved = [u for u in registry.unresolved_definitions() if u.startswith("benchmark_rate")]
    if unresolved:
        lines.append("UNRESOLVED definitions (prefer null): " + "; ".join(unresolved))
    return "\n".join(lines)


def _extraction_prompt(parsed: ParsedDocument, registry: Registry) -> str:
    return (
        _RULES
        + "\n## Registry context\n"
        + _registry_context(registry)
        + "\n\n## Document: "
        + Path(parsed.source_path).name
        + "\n"
        + parsed.render_for_llm()
    )


class _LangChainExtractor:
    def __init__(self, chat: object) -> None:
        self._structured = chat.with_structured_output(CostPlanExtraction)

    def extract(self, parsed: ParsedDocument, registry: Registry) -> CostPlanExtraction:
        prompt = _extraction_prompt(parsed, registry)
        try:
            result = self._structured.invoke(prompt)
        except Exception as exc:  # endpoint down, model missing, bad output
            raise ModelUnavailable(f"extraction chain failed: {exc}") from exc
        if isinstance(result, CostPlanExtraction):
            return result
        return CostPlanExtraction.model_validate(result)


# ---------------------------------------------------------------------------
# Deterministic dev-only extractor (provider=stub)
# ---------------------------------------------------------------------------

_META_KEY_MAP = {
    "project": "project_name", "project name": "project_name",
    "date": "plan_date", "plan date": "plan_date",
    "stage": "stage",
    "area basis": "area_basis", "area": "area_basis",
    "gfa": "gfa_m2", "gfa m2": "gfa_m2", "gfa (m2)": "gfa_m2",
    "rate basis": "rate_basis",
    "sector": "sector",
    "location": "location",
    "currency": "currency",
    "basis notes": "basis_notes", "notes": "basis_notes",
}

_META_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z ()/]{2,24}?)\s*[:]\s*(.+?)\s*$")


class _StubExtractor:
    """Deterministic table parser used with PPM_MODEL_PROVIDER=stub.

    This is a plumbing check for synthetic data, not an extraction model.
    """

    def extract(self, parsed: ParsedDocument, registry: Registry) -> CostPlanExtraction:
        meta, warnings = self._metadata(parsed)
        elements: list[ElementExtraction] = []

        for table in parsed.tables:
            columns = self._classify(table.header)
            if columns is None:
                continue
            for index, row in enumerate(table.rows):
                element = self._row_to_element(table, row, index, columns)
                if element is not None:
                    elements.append(element)

        project_name = meta.get("project_name") or Path(parsed.source_path).stem
        if not meta.get("project_name"):
            warnings.append("project name not found in document - used filename")
        stage = meta.get("stage") or "unknown"
        if stage == "unknown":
            warnings.append("stage not found in document")

        confidence = round(sum(e.confidence for e in elements) / len(elements), 3) if elements else 0.0
        return CostPlanExtraction(
            project_name=project_name,
            sector=meta.get("sector"),
            stage=stage,
            plan_date=parse_date(meta.get("plan_date")),
            currency=meta.get("currency") or "IDR",
            area_basis=meta.get("area_basis"),
            gfa_m2=parse_number(meta.get("gfa_m2")),
            rate_basis=meta.get("rate_basis"),
            location=meta.get("location"),
            basis_notes=meta.get("basis_notes"),
            total_cost_idr=None,
            elements=elements,
            confidence=confidence,
            warnings=warnings,
        )

    def _metadata(self, parsed: ParsedDocument) -> tuple[dict[str, str], list[str]]:
        meta: dict[str, str] = {}
        warnings: list[str] = []
        candidates: list[str] = []
        for table in parsed.tables:
            for row in [*table.preamble, *table.rows]:
                candidates.extend(str(cell) for cell in row if cell is not None)
        candidates.extend(text.text for text in parsed.texts)
        for line in "\n".join(candidates).splitlines():
            match = _META_LINE_RE.match(line)
            if not match:
                continue
            key = " ".join(match.group(1).lower().split())
            field = _META_KEY_MAP.get(key)
            value = match.group(2).strip()
            if field and value and field not in meta:
                meta[field] = value
        return meta, warnings

    def _classify(self, header: list[str] | None) -> dict[str, int] | None:
        if not header:
            return None
        columns: dict[str, int] = {}
        for index, raw in enumerate(header):
            key = " ".join(str(raw or "").strip().lower().split())
            if not key:
                continue
            if "description" in key or key in {"item", "element", "desc", "work item"}:
                columns.setdefault("description", index)
            elif "code" in key:
                columns.setdefault("code", index)
            elif "qty" in key or "quantity" in key:
                columns.setdefault("qty", index)
            elif "unit" in key and "rate" not in key:
                columns.setdefault("unit", index)
            elif "rate" in key:
                columns.setdefault("rate", index)
            elif "amount" in key or "total" in key:
                columns.setdefault("amount", index)
            elif "spec" in key or "class" in key:
                columns.setdefault("spec_level", index)
        if "description" not in columns:
            return None
        if "rate" not in columns and "amount" not in columns:
            return None
        return columns

    def _row_to_element(self, table, row: list[object], index: int, columns: dict[str, int]) -> ElementExtraction | None:
        def cell(field: str) -> object | None:
            position = columns.get(field)
            if position is None or position >= len(row):
                return None
            return row[position]

        description = str(cell("description") or "").strip()
        if not description or description.lower().startswith(("total", "subtotal", "sub-total")):
            return None

        rate = parse_number(cell("rate"))
        amount = parse_number(cell("amount"))
        qty = parse_number(cell("qty"))
        unit = str(cell("unit") or "").strip() or None
        code = str(cell("code") or "").strip() or None
        spec_raw = str(cell("spec_level") or "").strip().lower() or None
        spec = spec_raw if spec_raw in {"low", "medium", "high"} else None
        confidence = 0.95 if (unit and (rate or amount)) else 0.55

        return ElementExtraction(
            code=code,
            description=description,
            qty=qty,
            unit=unit,
            rate_idr=rate,
            amount_idr=amount,
            spec_level=spec,
            confidence=confidence,
            source_ref=table.row_ref(index),
        )


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

class _HashEmbeddings:
    """Deterministic pseudo-vectors for the stub provider. Similarity is
    meaningless by construction; used only to exercise retrieval plumbing."""

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        values: list[float] = []
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        while len(values) < self.dim:
            digest = hashlib.sha256(digest).digest()
            values.extend(byte / 127.5 - 1.0 for byte in digest)
        vector = values[: self.dim]
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


def get_embedder(settings: Settings):
    if settings.provider == "stub":
        return _HashEmbeddings(settings.embed_dim)
    if settings.provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(model=settings.embed_model, base_url=settings.ollama_host)
    if settings.provider == "api":
        if not settings.api_base:
            raise ModelUnavailable("PPM_API_BASE is not set for provider=api")
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model=settings.embed_model, base_url=settings.api_base, api_key=settings.api_key)
    raise ModelUnavailable(f"unknown provider '{settings.provider}'")


def _check_dimension(settings: Settings, vector: list[float]) -> list[float]:
    if len(vector) != settings.embed_dim:
        raise ModelUnavailable(
            f"embedding dimension {len(vector)} != PPM_EMBED_DIM {settings.embed_dim}; "
            f"check PPM_EMBED_MODEL '{settings.embed_model}' and the migration's vector(N)"
        )
    return vector


def embed_texts(settings: Settings, texts: list[str]) -> list[list[float]]:
    embedder = get_embedder(settings)
    try:
        vectors = embedder.embed_documents(texts)
    except Exception as exc:
        raise ModelUnavailable(f"embedding endpoint failed: {exc}") from exc
    return [_check_dimension(settings, v) for v in vectors]


def embed_query(settings: Settings, text: str) -> list[float]:
    embedder = get_embedder(settings)
    try:
        vector = embedder.embed_query(text)
    except Exception as exc:
        raise ModelUnavailable(f"embedding endpoint failed: {exc}") from exc
    return _check_dimension(settings, vector)


# ---------------------------------------------------------------------------
# Factories and diagnostics
# ---------------------------------------------------------------------------

def get_extractor(settings: Settings) -> Extractor:
    if settings.provider == "stub":
        return _StubExtractor()
    if settings.provider == "ollama":
        from langchain_ollama import ChatOllama

        chat = ChatOllama(model=settings.chat_model, base_url=settings.ollama_host, temperature=0)
        return _LangChainExtractor(chat)
    if settings.provider == "api":
        if not settings.api_base or not settings.api_key:
            raise ModelUnavailable("PPM_API_BASE and PPM_API_KEY are required for provider=api")
        from langchain_openai import ChatOpenAI

        chat = ChatOpenAI(model=settings.chat_model, base_url=settings.api_base, api_key=settings.api_key, temperature=0)
        return _LangChainExtractor(chat)
    raise ModelUnavailable(f"unknown provider '{settings.provider}'")


def check_models(settings: Settings) -> dict:
    """Diagnostics for `ppm models check`."""
    report: dict = {"provider": settings.provider, "residency": settings.residency, "missing": [], "ok": False}
    if settings.provider == "stub":
        report["ok"] = True
        report["note"] = "stub provider: deterministic dev-only, never ingest real data with it"
        return report
    if settings.provider == "api":
        report["ok"] = bool(settings.api_base and settings.api_key)
        if not report["ok"]:
            report["missing"] = ["PPM_API_BASE", "PPM_API_KEY"]
        return report

    try:
        with urlopen(f"{settings.ollama_host.rstrip('/')}/api/tags", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, OSError, json.JSONDecodeError) as exc:
        report["missing"] = [settings.chat_model, settings.embed_model]
        report["error"] = f"Ollama not reachable at {settings.ollama_host}: {exc}"
        return report

    names = {m.get("name", "") for m in payload.get("models", [])}
    names |= {n.split(":")[0] for n in names}
    needed = [settings.chat_model, settings.embed_model]
    report["missing"] = [n for n in needed if n not in names and n.split(":")[0] not in names]
    report["ok"] = not report["missing"]
    if report["missing"]:
        report["hint"] = "pull them on the office machine: " + "; ".join(f"ollama pull {n}" for n in report["missing"])
    return report
