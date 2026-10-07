"""Rate normalisation (plan section 6, step 3): units to canonical form,
Indonesian number/date formats, rate derivation, client-identity redaction.
Pure functions - no DB, no LLM, fully unit-testable."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from ppm.registry import Registry
from ppm.schemas import (
    CostPlanExtraction,
    NormalisedRate,
    ValidationIssue,
)

_NUMBER_RE = re.compile(r"-?[\d.,]+")
_CURRENCY_RE = re.compile(r"(?i)(rp|idr|usd|eur|\$|€|£)")
_THOUSANDS_3_RE = re.compile(r"-?\d{1,3}([,.]\d{3})+")

_DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
    "%d %B %Y", "%d %b %Y", "%B %Y", "%b %Y", "%m/%Y",
)


def parse_number(value: object) -> float | None:
    """Parse numbers as an Indonesian office writes them.

    '1.234,56' -> 1234.56 ; '1,234.56' -> 1234.56 ; '1.234' -> 1234 ;
    'Rp 1.500.000' -> 1500000.0 ; 1234.5 -> 1234.5
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("\u00a0", " ")
    if not text:
        return None
    text = _CURRENCY_RE.sub("", text).replace(" ", "")
    if not _NUMBER_RE.fullmatch(text):
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", "") if _THOUSANDS_3_RE.fullmatch(text) else text.replace(",", ".")
    elif "." in text and _THOUSANDS_3_RE.fullmatch(text):
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def parse_date(value: object) -> date | None:
    """Parse plan dates; day-first formats win (Indonesian convention)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def round_idr(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def redact(text: str, blocklist: list[str]) -> str:
    """Second, deterministic pass over client identity stripping (the first is
    the extraction prompt; see registry/entities/document.yaml)."""
    out = text
    for name in blocklist:
        out = re.sub(re.escape(name), "[redacted]", out, flags=re.IGNORECASE)
    return out


def derive_rate(rate_raw: object, amount_raw: object, qty_raw: object) -> int | None:
    rate = parse_number(rate_raw)
    if rate is not None and rate > 0:
        return round_idr(rate)
    amount = parse_number(amount_raw)
    qty = parse_number(qty_raw)
    if amount is not None and qty and qty > 0:
        return round_idr(amount / qty)
    return None


def normalise_elements(
    extraction: CostPlanExtraction,
    registry: Registry,
    review_threshold: float,
    blocklist: list[str] | None = None,
) -> tuple[list[NormalisedRate], list[ValidationIssue], int]:
    """Normalise extraction elements into BenchmarkRate rows.

    Returns (rows, issues, dropped_count). Row-level errors drop the row;
    warnings are recorded and the row passes.
    """
    blocklist = blocklist or []
    spec_levels = registry.spec_levels()
    valid_sectors = registry.enum_values("project", "sector")
    rows: list[NormalisedRate] = []
    issues: list[ValidationIssue] = []
    dropped = 0

    plan_date = parse_date(extraction.plan_date)
    sector = extraction.sector if extraction.sector in valid_sectors else None
    if extraction.sector and sector is None:
        issues.append(ValidationIssue(
            severity="warning", row_index=None, field="sector",
            message=f"sector '{extraction.sector}' not in registry enum - stored unclassified",
        ))

    for index, element in enumerate(extraction.elements):
        description = redact((element.description or "").strip(), blocklist)
        if not description:
            issues.append(ValidationIssue(
                severity="error", row_index=index, field="description",
                message="element has no description - dropped",
            ))
            dropped += 1
            continue

        rate_idr = derive_rate(element.rate_idr, element.amount_idr, element.qty)
        if rate_idr is None:
            issues.append(ValidationIssue(
                severity="error", row_index=index, field="rate_idr",
                message="no rate derivable (need a positive rate, or amount with qty) - dropped",
            ))
            dropped += 1
            continue

        unit = registry.canonical_unit(element.unit)
        if unit is None:
            problem = f"unit '{element.unit}' not in registry unit map" if element.unit else "unit missing on the row"
            issues.append(ValidationIssue(
                severity="error", row_index=index, field="unit",
                message=f"{problem} - dropped",
            ))
            dropped += 1
            continue

        if plan_date is None:
            issues.append(ValidationIssue(
                severity="error", row_index=index, field="plan_date",
                message="no plan date on the document - cannot normalise",
            ))
            dropped += 1
            continue

        code_known = registry.is_known_element_code(element.code)
        if not code_known:
            issues.append(ValidationIssue(
                severity=registry.unknown_code_severity(), row_index=index, field="element_code",
                message=f"element code '{element.code}' not in the signed code list"
                        + ("" if registry.element_codes_resolved() else " (list unresolved - definition fight 3)"),
            ))

        spec_level = element.spec_level if element.spec_level in spec_levels else None
        if element.spec_level and spec_level is None:
            issues.append(ValidationIssue(
                severity="warning", row_index=index, field="spec_level",
                message=f"spec level '{element.spec_level}' not in registry - rendered unclassified",
            ))

        rate = parse_number(element.rate_idr)
        amount = parse_number(element.amount_idr)
        qty = parse_number(element.qty)
        if rate and qty and amount and amount > 0:
            derived = amount / qty
            if abs(derived - rate) / max(rate, 1) > 0.05:
                issues.append(ValidationIssue(
                    severity="warning", row_index=index, field="amount_idr",
                    message=f"amount ({amount:,.0f}) disagrees with rate x qty ({derived:,.0f})",
                ))

        if element.confidence < review_threshold:
            issues.append(ValidationIssue(
                severity="warning", row_index=index, field="confidence",
                message=f"confidence {element.confidence:.2f} below threshold {review_threshold:.2f}",
            ))

        rows.append(NormalisedRate(
            element_code=(element.code or "UNCODED").strip().upper(),
            description=description,
            rate_idr=rate_idr,
            unit=unit,
            date_normalised_to=plan_date,
            location=(extraction.location or None),
            sector=sector,
            spec_level=spec_level,
            client_identifiable=False,
            source_ref=element.source_ref,
        ))

    return rows, issues, dropped
