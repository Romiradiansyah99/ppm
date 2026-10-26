"""Planner: turn a domain question into a validated AnalysisPlan."""

from __future__ import annotations

import json
import re

from ppm.analyst.catalog import catalog_text, sample_rows
from ppm.analyst.codegen import OpenAICompatibleCodegen
from ppm.analyst.schemas import AnalysisPlan, validate_plan
from ppm.config import Settings


_PLAN_RULES = """You are the planner of PPM Analyst, a project-management research analyst.

Return ONLY a JSON object matching this schema:
{schema}

Rules:
- Use only the `analyst_*` views and columns in the catalog below. CTEs are allowed.
- `datasets` contains SELECT queries over the catalog. A dataset has a name,
  source "sql", sql, a `schema` object (column -> type), and inspection checks.
- Prefer `description` for human-readable component names; `element_code` is a code.
- Every task's `output_schema` must match the DataFrame columns its code returns.
- Keep plans small: one dataset and 1-5 tasks is usually enough.
- `tasks` is a DAG. Each task has id, name, kind (dataframe|chart|table),
  depends_on (task ids), description, output_schema (column -> type), and
  validation.
- Allowed validation checks: non_empty, no_negative_amount, currency_consistent,
  unit_in_registry, rate_matches_amount, sum_reconciles, basis_declared.
- `sum_reconciles` requires exactly these params: group_col, value_col,
  reference_table, reference_key, reference_col. The task output MUST contain
  group_col and value_col. Example:
  {{"check": "sum_reconciles", "params": {{"group_col": "costplan_id",
   "value_col": "total_cost", "reference_table": "analyst_cost_plan",
   "reference_key": "costplan_id", "reference_col": "total_cost",
   "tolerance": 0.005}}}}
- Use `currency_consistent` only for single-currency outputs. If the task
  compares multiple currencies, keep a `currency` column and OMIT that check.
- Use `basis_declared` only when computing cost per m2, and pass
  params.basis as GFA, NIA or IPMS. Omit it for total-cost tasks.
- Each dataset `sql` must be exactly ONE SELECT or WITH...SELECT statement.
  No semicolons and no multiple statements.
- Never invent numbers. Every result must come from SQL + code.
- Do not block on a missing FX rate. State a conversion assumption, or compare
  within each currency when no rate is available.
- If area_basis is null, do not compute cost per m2; state that in assumptions.
- Prefer 1-5 tasks. The plan must be executable, not a wish list.
- Dates are ISO (YYYY-MM-DD). Currency values are ISO codes.

Catalog:
{catalog}

Examples of good questions: highest cost component, cost/m2 comparison between
projects, variance between cost plan versions, rate benchmark, risk exposure,
change impact, overdue deliverables.

Question:
{question}
"""


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def plan_question(question: str, settings: Settings, access_level: str = "all") -> AnalysisPlan:
    codegen = OpenAICompatibleCodegen(settings)
    schema = json.dumps(AnalysisPlan.model_json_schema(), indent=2)
    prompt = _PLAN_RULES.format(
        schema=schema, catalog=catalog_text(access_level), question=question
    )
    prompt += f"\nThe user's data scope is `{access_level}`. Restricted projects are invisible.\n"
    content = codegen._chat([{"role": "user", "content": prompt}], json_mode=True)
    plan = AnalysisPlan.model_validate_json(_extract_json(content))
    return validate_plan(plan)


def input_samples(dataset_names: list[str], access_level: str = "all") -> dict[str, list[dict]]:
    samples: dict[str, list[dict]] = {}
    for name in dataset_names:
        try:
            samples[name] = sample_rows(name, limit=3, access_level=access_level)
        except Exception:
            continue
    return samples
