import pandas as pd
import pytest

from ppm.analyst.validators import (
    check_currency_consistent,
    check_no_negative_amount,
    check_rate_matches_amount,
    check_sum_reconciles,
    check_unit_in_registry,
)


def test_currency_global_mixing_fails():
    frame = pd.DataFrame({"currency": ["SGD", "USD"]})
    result = check_currency_consistent(frame, {})
    assert result.passed is False


def test_currency_grouped_mixing_passes():
    frame = pd.DataFrame({
        "costplan_id": ["a", "a", "b", "b"],
        "currency": ["SGD", "SGD", "USD", "USD"],
    })
    result = check_currency_consistent(frame, {"group_col": "costplan_id"})
    assert result.passed is True


def test_negative_amount_fails():
    frame = pd.DataFrame({"amount": [10, -5]})
    assert check_no_negative_amount(frame, {}).passed is False


def test_rate_matches_amount():
    frame = pd.DataFrame({"rate": [100, 100], "qty": [2, 3], "amount": [200, 300]})
    assert check_rate_matches_amount(frame, {}).passed is True


def test_rate_mismatch_fails():
    frame = pd.DataFrame({"rate": [100], "qty": [2], "amount": [999]})
    assert check_rate_matches_amount(frame, {}).passed is False


def test_unit_registry():
    frame = pd.DataFrame({"unit": ["m2", "m3"]})
    assert check_unit_in_registry(frame, {}).passed is True
    assert check_unit_in_registry(pd.DataFrame({"unit": ["furlong"]}), {}).passed is False


@pytest.mark.db
def test_sum_reconciles_against_cost_plan(clean_db):
    from ppm.data.seed import seed_defaults

    seed_defaults()
    from ppm import db

    row = db.fetch_one(
        "SELECT cp.costplan_id::text AS costplan_id, cp.total_cost, sum(cl.amount) AS line_total "
        "FROM cost_plan cp JOIN cost_line cl ON cl.costplan_id = cp.costplan_id "
        "GROUP BY cp.costplan_id, cp.total_cost ORDER BY cp.total_cost DESC LIMIT 1"
    )
    frame = pd.DataFrame([{
        "costplan_id": row["costplan_id"],
        "line_total": float(row["line_total"]),
    }])
    result = check_sum_reconciles(frame, {
        "group_col": "costplan_id", "value_col": "line_total",
        "reference_table": "cost_plan", "reference_key": "costplan_id",
        "reference_col": "total_cost", "tolerance": 0.005,
    })
    assert result.passed is True
