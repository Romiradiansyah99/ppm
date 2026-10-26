"""Deterministic validators. These run in ordinary Python, not in an agent.

A task cannot skip validation: the executor calls this module for every output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from ppm import db

VALID_UNITS = {"m2", "m3", "m", "nr", "sum", "hour", "day", "kg", "ton", "m2/month"}


@dataclass
class ValidationResult:
    check: str
    passed: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _fail(check: str, message: str, **details: Any) -> ValidationResult:
    return ValidationResult(check=check, passed=False, message=message, details=details)


def _ok(check: str, message: str = "ok", **details: Any) -> ValidationResult:
    return ValidationResult(check=check, passed=True, message=message, details=details)


def check_schema(df: pd.DataFrame, required: list[str], check: str = "schema") -> ValidationResult:
    missing = [column for column in required if column not in df.columns]
    if missing:
        return _fail(check, f"missing columns: {missing}", columns=list(df.columns))
    return _ok(check, columns=required)


def check_non_empty(df: pd.DataFrame, check: str = "non_empty") -> ValidationResult:
    if df.empty:
        return _fail(check, "output has zero rows")
    return _ok(check, rows=len(df))


def check_no_all_null(df: pd.DataFrame, required: list[str], check: str = "no_all_null") -> ValidationResult:
    empty = [column for column in required if column in df.columns and df[column].isna().all()]
    if empty:
        return _fail(check, f"columns are entirely null: {empty}")
    return _ok(check)


def check_no_negative_amount(df: pd.DataFrame, params: dict, check: str = "no_negative_amount") -> ValidationResult:
    column = params.get("column", "amount")
    if column not in df.columns:
        return _fail(check, f"column '{column}' not present")
    tolerance = float(params.get("tolerance", 0.01))
    bad = df[pd.to_numeric(df[column], errors="coerce") < -tolerance]
    if not bad.empty:
        return _fail(check, f"{len(bad)} row(s) have a negative {column}", rows=bad.head(5).to_dict("records"))
    return _ok(check)


def check_currency_consistent(df: pd.DataFrame, params: dict, check: str = "currency_consistent") -> ValidationResult:
    column = params.get("column", "currency")
    group_col = params.get("group_col")
    if column not in df.columns:
        return _ok(check, "no currency column")
    if group_col and group_col in df.columns:
        bad = {}
        for key, group in df.groupby(group_col, dropna=False):
            values = sorted({str(value) for value in group[column].dropna().unique()})
            if len(values) > 1:
                bad[str(key)] = values
        if bad:
            return _fail(check, f"mixed currencies within {len(bad)} group(s)", groups=bad)
        return _ok(check, groups=int(df[group_col].nunique()))
    values = sorted({str(value) for value in df[column].dropna().unique()})
    if len(values) > 1:
        return _fail(check, f"mixed currencies: {values}", currencies=values)
    return _ok(check, currencies=values)


def check_unit_in_registry(df: pd.DataFrame, params: dict, check: str = "unit_in_registry") -> ValidationResult:
    column = params.get("column", "unit")
    if column not in df.columns:
        return _ok(check, "no unit column")
    values = {str(value).strip().lower() for value in df[column].dropna().unique()}
    unknown = sorted(values - VALID_UNITS)
    if unknown:
        return _fail(check, f"units outside registry: {unknown}", units=unknown)
    return _ok(check, units=sorted(values))


def check_rate_matches_amount(df: pd.DataFrame, params: dict, check: str = "rate_matches_amount") -> ValidationResult:
    columns = {"rate": params.get("rate", "rate"), "amount": params.get("amount", "amount"), "qty": params.get("qty", "qty")}
    missing = [column for column in columns.values() if column not in df.columns]
    if missing:
        return _fail(check, f"missing columns: {missing}")
    frame = df.dropna(subset=list(columns.values()))
    if frame.empty:
        return _ok(check, "no complete rate/qty/amount rows")
    tolerance = float(params.get("tolerance", 0.01))
    expected = pd.to_numeric(frame[columns["rate"]], errors="coerce") * pd.to_numeric(frame[columns["qty"]], errors="coerce")
    actual = pd.to_numeric(frame[columns["amount"]], errors="coerce")
    bad = (expected - actual).abs() > (actual.abs() * tolerance).clip(lower=1.0)
    if bad.any():
        return _fail(check, f"{int(bad.sum())} row(s) do not reconcile", examples=frame[bad].head(5).to_dict("records"))
    return _ok(check, checked=len(frame))


def check_sum_reconciles(df: pd.DataFrame, params: dict, check: str = "sum_reconciles") -> ValidationResult:
    group_col = params.get("group_col")
    value_col = params.get("value_col")
    reference_table = params.get("reference_table")
    reference_key = params.get("reference_key", "costplan_id")
    reference_col = params.get("reference_col", "total_cost")
    tolerance = float(params.get("tolerance", 0.005))

    missing_params = [
        name for name, value in (
            ("group_col", group_col),
            ("value_col", value_col),
            ("reference_table", reference_table),
        ) if not value
    ]
    if missing_params:
        return _fail(
            check,
            f"sum_reconciles requires {', '.join(missing_params)}",
            params=params,
        )
    if group_col not in df.columns or value_col not in df.columns:
        return _fail(check, f"missing columns: {group_col}, {value_col}")

    totals = df.groupby(group_col, dropna=False)[value_col].apply(
        lambda series: pd.to_numeric(series, errors="coerce").sum()
    )
    keys = [str(key) for key in totals.index]
    rows = db.fetch_all(
        f"SELECT {reference_key}::text AS key, {reference_col} AS reference "
        f"FROM {reference_table} WHERE {reference_key}::text = ANY(%s)",
        (keys,),
    )
    references = {row["key"]: float(row["reference"] or 0) for row in rows}
    problems = []
    for key, total in totals.items():
        reference = references.get(str(key))
        if reference is None:
            problems.append({"key": str(key), "problem": "no reference row"})
            continue
        allowed = max(abs(reference) * tolerance, 1.0)
        if abs(float(total) - reference) > allowed:
            problems.append({
                "key": str(key),
                "line_total": float(total),
                "reference": reference,
                "difference": float(total) - reference,
            })
    if problems:
        return _fail(check, f"{len(problems)} group(s) do not reconcile", problems=problems[:5])
    return _ok(check, groups=len(totals))


def check_basis_declared(df: pd.DataFrame, params: dict, check: str = "basis_declared") -> ValidationResult:
    basis = params.get("basis")
    area_columns = [
        column for column in df.columns
        if re.search(r"(m2|sqm|area|gfa|nia)", str(column), re.IGNORECASE)
    ]
    if not basis and not area_columns:
        return _ok(check, basis="not applicable")
    if not basis:
        return _fail(check, "cost/m2 analysis requires a declared area basis (GFA/NIA/IPMS)")
    return _ok(check, basis=basis)


VALIDATORS: dict[str, Callable[[pd.DataFrame, dict], ValidationResult]] = {
    "non_empty": lambda df, params: check_non_empty(df, params.get("check", "non_empty")),
    "no_negative_amount": lambda df, params: check_no_negative_amount(df, params),
    "currency_consistent": lambda df, params: check_currency_consistent(df, params),
    "unit_in_registry": lambda df, params: check_unit_in_registry(df, params),
    "rate_matches_amount": lambda df, params: check_rate_matches_amount(df, params),
    "sum_reconciles": lambda df, params: check_sum_reconciles(df, params),
    "basis_declared": lambda df, params: check_basis_declared(df, params),
}


def validate_output(df: pd.DataFrame, task, context: dict | None = None) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    required = list(task.output_schema.keys())
    results.append(check_schema(df, required))
    results.append(check_non_empty(df))
    results.append(check_no_all_null(df, required))
    for spec in task.validation:
        if spec.check in {"schema", "non_empty", "no_all_null"}:
            continue
        validator = VALIDATORS.get(spec.check)
        if validator is None:
            results.append(_fail(spec.check, f"unknown validator '{spec.check}'"))
            continue
        results.append(validator(df, spec.params))
    return results
