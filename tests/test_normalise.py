"""Normalisation: Indonesian number/date formats, unit mapping, rate
derivation, redaction, the review/threshold policy."""

from __future__ import annotations

from datetime import date

from ppm.normalise import (
    derive_rate,
    normalise_elements,
    parse_date,
    parse_number,
    redact,
    round_idr,
)
from ppm.schemas import CostPlanExtraction, ElementExtraction


def test_parse_number_indonesian_and_western():
    assert parse_number("1.234,56") == 1234.56
    assert parse_number("1,234.56") == 1234.56
    assert parse_number("1.234") == 1234.0
    assert parse_number("1,234") == 1234.0
    assert parse_number("Rp 1.500.000") == 1500000.0
    assert parse_number("2,5") == 2.5
    assert parse_number(1234.5) == 1234.5
    assert parse_number(7) == 7.0


def test_parse_number_rejects_junk():
    assert parse_number(None) is None
    assert parse_number("") is None
    assert parse_number("approx 1200") is None
    assert parse_number("n/a") is None
    assert parse_number(True) is None


def test_parse_date_variants():
    assert parse_date("2024-03-15") == date(2024, 3, 15)
    assert parse_date("15/03/2024") == date(2024, 3, 15)
    assert parse_date("15-03-2024") == date(2024, 3, 15)
    assert parse_date("March 2024") == date(2024, 3, 1)
    assert parse_date("15 March 2024") == date(2024, 3, 15)
    assert parse_date("garbage") is None


def test_derive_rate_prefers_stated_rate_then_amount_over_qty():
    assert derive_rate(45000, 999999, 2) == 45000
    assert derive_rate(None, 90000, 2) == 45000
    assert derive_rate(None, 90000, 0) is None
    assert derive_rate(None, None, 5) is None
    assert derive_rate(-5, None, None) is None


def test_round_idr_half_up():
    assert round_idr(0.5) == 1
    assert round_idr(1.5) == 2
    assert round_idr(1500500.4) == 1500500


def test_redact_is_case_insensitive():
    assert redact("Fit-out for PT Synthetic Client HQ", ["PT SYNTHETIC CLIENT"]) == "Fit-out for [redacted] HQ"


def _extraction(elements: list[ElementExtraction], **overrides) -> CostPlanExtraction:
    base = dict(
        project_name="PPM TEST",
        stage="elemental",
        plan_date=date(2024, 3, 15),
        elements=elements,
        confidence=0.9,
    )
    base.update(overrides)
    return CostPlanExtraction(**base)


def _element(**overrides) -> ElementExtraction:
    base = dict(description="Rebar", unit="kg", qty=1000, rate_idr=18000,
                confidence=0.95, source_ref="Cost Plan!R12")
    base.update(overrides)
    return ElementExtraction(**base)


def test_normalise_full_row(registry):
    rows, issues, dropped = normalise_elements(_extraction([_element()]), registry, 0.7)
    assert dropped == 0
    assert len(rows) == 1
    row = rows[0]
    assert row.rate_idr == 18000
    assert row.unit == "kg"
    assert row.date_normalised_to == date(2024, 3, 15)
    unknown_code = [i for i in issues if i.field == "element_code"]
    assert unknown_code and unknown_code[0].severity == "warning"


def test_normalise_derives_rate_from_amount(registry):
    element = _element(rate_idr=None, amount_idr=3_600_000, qty=200)
    rows, _, dropped = normalise_elements(_extraction([element]), registry, 0.7)
    assert dropped == 0
    assert rows[0].rate_idr == 18000


def test_normalise_drops_unknown_unit(registry):
    rows, issues, dropped = normalise_elements(_extraction([_element(unit="banana")]), registry, 0.7)
    assert dropped == 1 and not rows
    assert any(i.severity == "error" and i.field == "unit" for i in issues)


def test_normalise_drops_underivable_rate(registry):
    rows, issues, dropped = normalise_elements(
        _extraction([_element(rate_idr=None, amount_idr=None, qty=None)]), registry, 0.7
    )
    assert dropped == 1 and not rows
    assert any(i.severity == "error" and i.field == "rate_idr" for i in issues)


def test_normalise_flags_low_confidence(registry):
    rows, issues, dropped = normalise_elements(_extraction([_element(confidence=0.4)]), registry, 0.7)
    assert dropped == 0 and len(rows) == 1
    assert any(i.field == "confidence" for i in issues)


def test_normalise_redacts_client_and_invalid_sector(registry):
    element = _element(description="Works for PT Synthetic Client tower")
    rows, issues, _ = normalise_elements(
        _extraction([element], sector="space-station"), registry, 0.7,
        blocklist=registry.redaction_blocklist(),
    )
    assert rows[0].description == "Works for [redacted] tower"
    assert rows[0].sector is None
    assert any(i.field == "sector" and i.severity == "warning" for i in issues)


def test_normalise_amount_disagreement_warning(registry):
    element = _element(rate_idr=18000, amount_idr=5_000_000, qty=100)  # 18000*100 != 50000
    _, issues, _ = normalise_elements(_extraction([element]), registry, 0.7)
    assert any(i.field == "amount_idr" for i in issues)


def test_normalise_missing_plan_date_drops_rows(registry):
    rows, issues, dropped = normalise_elements(_extraction([_element()], plan_date=None), registry, 0.7)
    assert dropped == 1 and not rows
    assert any(i.field == "plan_date" for i in issues)
