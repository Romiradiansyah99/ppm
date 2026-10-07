"""Pydantic schemas: the extraction contract and the rows the ingest graph
writes. Everything crossing a graph node boundary is one of these (or plain
dicts via model_dump) so the Postgres checkpointer can persist state."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

SpecLevel = Literal["low", "medium", "high"]


class ElementExtraction(BaseModel):
    """One priced row as read from the source document."""

    code: str | None = None
    description: str
    qty: float | None = None
    unit: str | None = None
    rate_idr: float | None = None
    amount_idr: float | None = None
    spec_level: SpecLevel | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    source_ref: str
    warnings: list[str] = Field(default_factory=list)


class CostPlanExtraction(BaseModel):
    """Structured output of the extraction chain over one source document."""

    project_name: str
    sector: str | None = None
    stage: str
    plan_date: date | None = None
    currency: str = "IDR"
    area_basis: str | None = None
    gfa_m2: float | None = None
    rate_basis: str | None = None
    location: str | None = None
    basis_notes: str | None = None
    author: str | None = None
    total_cost_idr: float | None = None
    elements: list[ElementExtraction]
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    severity: Literal["error", "warning"]
    row_index: int | None = None
    field: str | None = None
    message: str


class ValidationReport(BaseModel):
    issues: list[ValidationIssue] = Field(default_factory=list)
    review_required: bool = False
    review_items: list[dict] = Field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]


class NormalisedRate(BaseModel):
    """A benchmark_rate row ready for insert."""

    element_code: str
    description: str
    rate_idr: int
    unit: str
    date_normalised_to: date
    location: str | None = None
    sector: str | None = None
    spec_level: SpecLevel | None = None
    client_identifiable: bool = False
    source_ref: str


class IngestStats(BaseModel):
    document_id: str | None = None
    costplan_id: str | None = None
    project_id: str | None = None
    elements_extracted: int = 0
    rates_written: int = 0
    rows_dropped: int = 0
    duplicates: int = 0
