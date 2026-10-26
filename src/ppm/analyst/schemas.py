"""Analysis plan schema and validation.

The plan is the analysis: datasets with SQL, a DAG of tasks with declared output
schemas, and deterministic validations. No code executes from an unvalidated plan.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|call|do|"
    r"vacuum|analyze|set|reset|merge|into)\b",
    re.IGNORECASE,
)
SQL_START = re.compile(r"^(select|with)\b", re.IGNORECASE)


class PlanError(Exception):
    pass


class DatasetSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    source: Literal["sql", "artifact"] = "sql"
    sql: str | None = None
    artifact_id: str | None = None
    schema_: dict[str, str] = Field(default_factory=dict, alias="schema")
    inspection: list[str] = Field(default_factory=lambda: ["row_count", "null_rate"])


class ValidationSpec(BaseModel):
    check: str
    task: str | None = None
    params: dict = Field(default_factory=dict)


class TaskSpec(BaseModel):
    id: str
    name: str
    kind: Literal["dataframe", "chart", "table"] = "dataframe"
    depends_on: list[str] = Field(default_factory=list)
    description: str
    output_schema: dict[str, str] = Field(default_factory=dict)
    validation: list[ValidationSpec] = Field(default_factory=list)
    code: str | None = None


class AnalysisPlan(BaseModel):
    question: str
    assumptions: list[str] = Field(default_factory=list)
    datasets: list[DatasetSpec]
    tasks: list[TaskSpec]
    output: Literal["report"] = "report"


def validate_sql(sql: str) -> str:
    cleaned = re.sub(r"--[^\n]*", "", sql)
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL).strip()
    cleaned = cleaned.rstrip(";").strip()
    if not SQL_START.match(cleaned):
        raise PlanError("SQL must start with SELECT or WITH")
    scrubbed = re.sub(r"'(?:''|[^'])*'", "''", cleaned)
    if ";" in scrubbed:
        raise PlanError("SQL must be a single statement")
    match = FORBIDDEN_SQL.search(scrubbed)
    if match:
        raise PlanError(f"SQL keyword is not allowed: {match.group(1).upper()}")
    return cleaned


def plan_layers(tasks: list[TaskSpec]) -> list[list[TaskSpec]]:
    task_map = {task.id: task for task in tasks}
    remaining = {task.id: set(task.depends_on) for task in tasks}
    layers: list[list[TaskSpec]] = []
    done: set[str] = set()

    while remaining:
        layer = [task_id for task_id, deps in remaining.items() if deps <= done]
        if not layer:
            raise PlanError("plan contains a cycle")
        layers.append([task_map[task_id] for task_id in layer])
        done.update(layer)
        for task_id in layer:
            remaining.pop(task_id)
    return layers


def validate_plan(plan: AnalysisPlan) -> AnalysisPlan:
    if not plan.question.strip():
        raise PlanError("question is empty")
    if not plan.datasets:
        raise PlanError("plan needs at least one dataset")
    if not plan.tasks:
        raise PlanError("plan needs at least one task")
    if len(plan.tasks) > 20:
        raise PlanError("plan has more than 20 tasks")

    dataset_names = [dataset.name for dataset in plan.datasets]
    if len(dataset_names) != len(set(dataset_names)):
        raise PlanError("duplicate dataset name")
    for dataset in plan.datasets:
        if dataset.source == "sql":
            if not dataset.sql:
                raise PlanError(f"dataset '{dataset.name}' has no SQL")
            validate_sql(dataset.sql)
        elif not dataset.artifact_id:
            raise PlanError(f"dataset '{dataset.name}' has no artifact_id")

    task_ids = [task.id for task in plan.tasks]
    if len(task_ids) != len(set(task_ids)):
        raise PlanError("duplicate task id")
    known = set(task_ids)
    for task in plan.tasks:
        if not task.output_schema:
            raise PlanError(f"task '{task.id}' has no output schema")
        missing = [dependency for dependency in task.depends_on if dependency not in known]
        if missing:
            raise PlanError(f"task '{task.id}' depends on unknown task(s): {missing}")
        if task.kind == "chart" and not task.depends_on:
            raise PlanError(f"chart task '{task.id}' must depend on a data task")
        for spec in task.validation:
            if spec.check == "sum_reconciles":
                missing_params = [
                    name for name in ("group_col", "value_col", "reference_table")
                    if not spec.params.get(name)
                ]
                if missing_params:
                    raise PlanError(
                        f"task '{task.id}' sum_reconciles is missing: {missing_params}"
                    )
            if spec.check == "basis_declared" and not spec.params.get("basis"):
                area_keys = [
                    key for key in task.output_schema
                    if re.search(r"(m2|sqm|area|gfa|nia)", key, re.IGNORECASE)
                ]
                if area_keys:
                    raise PlanError(
                        f"task '{task.id}' basis_declared needs params.basis"
                    )

    plan_layers(plan.tasks)
    return plan
