"""Deterministic demo plan used by `ppm analyst demo` and the tests."""

from __future__ import annotations

from ppm.analyst.codegen import StaticCodegen
from ppm.analyst.schemas import AnalysisPlan, DatasetSpec, TaskSpec, ValidationSpec

COMPONENT_CODE = """
import pandas as pd


def run(inputs: dict):
    df = inputs["cost_lines"].copy()
    out = df.groupby(["costplan_id", "project", "description"], as_index=False).agg(
        line_total=("amount", "sum"),
        rate=("rate", "max"),
        total_cost=("total_cost", "max"),
        currency=("currency", "first"),
    )
    return out
"""

TOP_CODE = """
import pandas as pd


def run(inputs: dict):
    df = inputs["by_component"].copy()
    out = df.sort_values("line_total", ascending=False).head(10).reset_index(drop=True)
    return out[["project", "description", "line_total", "rate", "total_cost", "currency"]]
"""

CHART_CODE = """
import matplotlib.pyplot as plt

import ppm_runtime


def run(inputs: dict):
    df = inputs["top_components"].copy()
    labels = (df["project"] + " | " + df["description"]).tolist()
    values = df["line_total"].tolist()
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.barh(labels[::-1], values[::-1])
    ax.set_xlabel("Amount")
    ax.set_title("Top cost components")
    return ppm_runtime.save_chart(fig, "top_cost_components")
"""


def demo_plan() -> AnalysisPlan:
    return AnalysisPlan(
        question="Komponen biaya apa yang paling besar lintas proyek benchmark, dan berapa rate per m2-nya?",
        assumptions=[
            "Rates are cost per m2 of GFA from the benchmark Cost Model sheet.",
            "SGD and USD projects are not converted; compare within each currency.",
            "Seed data is anonymised sample data, not live client projects.",
        ],
        datasets=[
            DatasetSpec(
                name="cost_lines",
                sql=(
                    "SELECT p.name AS project, cp.costplan_id::text AS costplan_id, "
                    "cl.description, cl.amount, cl.rate, cl.qty, cl.unit, cl.currency, "
                    "cp.total_cost, cp.gfa_m2, cl.source_ref "
                    "FROM analyst_cost_line cl "
                    "JOIN analyst_cost_plan cp ON cp.costplan_id = cl.costplan_id "
                    "JOIN analyst_project p ON p.project_id = cp.project_id "
                    "WHERE cl.amount IS NOT NULL"
                ),
                schema={
                    "project": "str", "costplan_id": "str", "description": "str",
                    "amount": "float", "rate": "float", "qty": "float", "unit": "str",
                    "currency": "str", "total_cost": "float", "gfa_m2": "float",
                    "source_ref": "str",
                },
                inspection=["row_count", "null_rate", "currency"],
            ),
            DatasetSpec(
                name="projects",
                sql="SELECT project_id::text, name, currency, gfa_m2, area_basis FROM analyst_project",
                schema={"project_id": "str", "name": "str", "currency": "str",
                        "gfa_m2": "float", "area_basis": "str"},
            ),
        ],
        tasks=[
            TaskSpec(
                id="by_component",
                name="Cost by project and component",
                kind="dataframe",
                depends_on=[],
                description="Aggregate line amounts by cost plan and component.",
                output_schema={
                    "costplan_id": "str", "project": "str", "description": "str",
                    "line_total": "float", "rate": "float", "total_cost": "float",
                    "currency": "str",
                },
                validation=[
                    ValidationSpec(check="sum_reconciles", params={
                        "group_col": "costplan_id", "value_col": "line_total",
                        "reference_table": "cost_plan", "reference_key": "costplan_id",
                        "reference_col": "total_cost", "tolerance": 0.005,
                    }),
                    ValidationSpec(check="no_negative_amount", params={"column": "line_total"}),
                    ValidationSpec(check="currency_consistent", params={
                        "column": "currency", "group_col": "costplan_id",
                    }),
                ],
                code=COMPONENT_CODE,
            ),
            TaskSpec(
                id="top_components",
                name="Top components",
                kind="dataframe",
                depends_on=["by_component"],
                description="Take the ten largest component totals.",
                output_schema={
                    "project": "str", "description": "str", "line_total": "float",
                    "rate": "float", "total_cost": "float", "currency": "str",
                },
                validation=[
                    ValidationSpec(check="no_negative_amount", params={"column": "line_total"}),
                ],
                code=TOP_CODE,
            ),
            TaskSpec(
                id="chart",
                name="Top components chart",
                kind="chart",
                depends_on=["top_components"],
                description="Horizontal bar chart of the ten largest components.",
                output_schema={"path": "str"},
                validation=[],
                code=CHART_CODE,
            ),
        ],
    )


def demo_codegen() -> StaticCodegen:
    return StaticCodegen({
        "Cost by project and component": COMPONENT_CODE,
        "Top components": TOP_CODE,
        "Top components chart": CHART_CODE,
    })
