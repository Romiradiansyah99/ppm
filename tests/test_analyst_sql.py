import pytest

from ppm.analyst.catalog import assert_allowed_tables
from ppm.analyst.schemas import PlanError, validate_sql


def test_allows_is_distinct_from_true():
    assert_allowed_tables(
        "SELECT * FROM analyst_cost_line WHERE is_subtotal IS DISTINCT FROM TRUE"
    )


def test_allows_extract_and_trim_from():
    assert_allowed_tables(
        "SELECT EXTRACT(YEAR FROM plan_date), TRIM(BOTH ' ' FROM currency) "
        "FROM analyst_cost_plan"
    )


def test_rejects_unknown_table():
    with pytest.raises(ValueError, match="not allowed"):
        assert_allowed_tables("SELECT * FROM secret_table")


def test_semicolon_inside_string_is_not_a_second_statement():
    sql = validate_sql("SELECT 'a;b' AS label FROM analyst_cost_line;")
    assert sql.endswith("'a;b' AS label FROM analyst_cost_line")


def test_semicolon_between_statements_is_rejected():
    with pytest.raises(PlanError, match="single statement"):
        validate_sql("SELECT 1; SELECT 2")


def test_quoted_keywords_are_not_rejected():
    assert validate_sql("SELECT 'update' AS word FROM analyst_cost_line")
