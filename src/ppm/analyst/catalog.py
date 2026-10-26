"""Schema catalog and read-only SQL access for the analyst.

Only role-filtered `analyst_*` views are exposed. The tool layer sets
`ppm.access_level` for the transaction, so a junior user cannot read restricted
rows even if the generated SQL asks for them.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ppm import db
from ppm.analyst.schemas import validate_sql

ALLOWED_TABLES = {
    "analyst_project": "Projects visible to the current user.",
    "analyst_cost_plan": "Cost plan versions visible to the current user.",
    "analyst_cost_line": "Cost plan line items visible to the current user.",
    "analyst_benchmark_rate": "Benchmark rates visible to the current user.",
    "analyst_risk": "Risk register entries visible to the current user.",
    "analyst_change_event": "Change events visible to the current user.",
    "analyst_deliverable": "Deliverables visible to the current user.",
    "analyst_document": "Documents visible to the current user.",
    "analyst_template": "Office template catalog (shared).",
}

TABLES = list(ALLOWED_TABLES)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def table_catalog(access_level: str = "baseline") -> list[dict[str, Any]]:
    rows = db.fetch_all(
        "SELECT table_name, column_name, data_type, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = ANY(%s) "
        "ORDER BY table_name, ordinal_position",
        (TABLES,),
    )
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["table_name"], []).append({
            "name": row["column_name"],
            "type": row["data_type"],
            "nullable": row["is_nullable"] == "YES",
        })
    catalog: list[dict[str, Any]] = []
    for table in TABLES:
        count_rows = run_sql(f"SELECT count(*) AS count FROM {table}", limit=1, access_level=access_level)
        count = count_rows[0]["count"] if count_rows else 0
        catalog.append({
            "table": table,
            "description": ALLOWED_TABLES[table],
            "rows": count,
            "columns": grouped.get(table, []),
        })
    return catalog


def catalog_text(access_level: str = "baseline") -> str:
    lines: list[str] = [f"Access scope: {access_level}", ""]
    for table in table_catalog(access_level):
        lines.append(f"VIEW {table['table']} ({table['rows']} rows) - {table['description']}")
        for column in table["columns"]:
            nullable = "" if column["nullable"] else " NOT NULL"
            lines.append(f"  {column['name']}: {column['type']}{nullable}")
    return "\n".join(lines)


def sample_rows(table: str, limit: int = 5, access_level: str = "baseline") -> list[dict[str, Any]]:
    if table not in ALLOWED_TABLES:
        raise ValueError(f"table '{table}' is not allowed")
    return run_sql(f"SELECT * FROM {table}", limit=limit, access_level=access_level)


TABLE_REF_RE = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][\w.]*)", re.IGNORECASE)
CTE_RE = re.compile(r"(?:\bwith\b|,)\s*([a-zA-Z_][\w]*)\s+as\s*\(", re.IGNORECASE)
SQL_KEYWORDS = {"true", "false", "null", "only", "lateral", "unnest", "select", "values"}


def _strip_non_table_from_clauses(sql: str) -> str:
    """Remove FROM clauses that are not table references (EXTRACT, TRIM, ...)."""
    scrubbed = re.sub(r"'(?:''|[^'])*'", "''", sql)
    scrubbed = re.sub(
        r"\bIS\s+(?:NOT\s+)?DISTINCT\s+FROM\b", "IS DISTINCT", scrubbed, flags=re.IGNORECASE
    )
    scrubbed = re.sub(
        r"\bEXTRACT\s*\([^)]*?\bFROM\b[^)]*\)", "EXTRACT(0)", scrubbed,
        flags=re.IGNORECASE | re.DOTALL,
    )
    scrubbed = re.sub(
        r"\b(?:TRIM|SUBSTRING)\s*\([^)]*?\bFROM\b[^)]*\)", "TRIM(0)", scrubbed,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return scrubbed


def assert_allowed_tables(cleaned: str) -> None:
    ctes = {name.lower() for name in CTE_RE.findall(cleaned)}
    for reference in TABLE_REF_RE.findall(_strip_non_table_from_clauses(cleaned)):
        table = reference.split(".")[-1].lower()
        if table in SQL_KEYWORDS:
            continue
        if table in ctes or table in ALLOWED_TABLES:
            continue
        raise ValueError(
            f"table '{reference}' is not allowed; use one of {sorted(ALLOWED_TABLES)}"
        )


def run_sql(sql: str, limit: int = 5000, access_level: str = "baseline") -> list[dict[str, Any]]:
    cleaned = validate_sql(sql)
    assert_allowed_tables(cleaned)
    safe_limit = max(1, min(int(limit), 100_000))
    wrapped = f"SELECT * FROM ({cleaned}) AS analyst_src LIMIT {safe_limit}"
    rows = db.fetch_all_access(access_level, wrapped)
    return [{key: _jsonable(value) for key, value in row.items()} for row in rows]


def rows_to_json(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, ensure_ascii=False, default=str)
