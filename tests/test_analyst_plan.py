import pytest

from ppm.analyst.schemas import (
    AnalysisPlan,
    DatasetSpec,
    PlanError,
    TaskSpec,
    plan_layers,
    validate_plan,
)


def _plan(tasks):
    return AnalysisPlan(
        question="test",
        datasets=[DatasetSpec(name="d", sql="SELECT 1 AS x")],
        tasks=tasks,
    )


def test_valid_plan_and_layers():
    plan = _plan([
        TaskSpec(id="a", name="a", description="a", output_schema={"x": "int"}),
        TaskSpec(id="b", name="b", description="b", depends_on=["a"], output_schema={"y": "int"}),
        TaskSpec(id="c", name="c", description="c", depends_on=["a"], output_schema={"z": "int"}),
    ])
    validate_plan(plan)
    layers = plan_layers(plan.tasks)
    assert [task.id for task in layers[0]] == ["a"]
    assert {task.id for task in layers[1]} == {"b", "c"}


def test_cycle_is_rejected():
    plan = _plan([
        TaskSpec(id="a", name="a", description="a", depends_on=["b"], output_schema={"x": "int"}),
        TaskSpec(id="b", name="b", description="b", depends_on=["a"], output_schema={"y": "int"}),
    ])
    with pytest.raises(PlanError, match="cycle"):
        validate_plan(plan)


def test_write_sql_is_rejected():
    plan = AnalysisPlan(
        question="test",
        datasets=[DatasetSpec(name="d", sql="DELETE FROM project")],
        tasks=[TaskSpec(id="a", name="a", description="a", output_schema={"x": "int"})],
    )
    with pytest.raises(PlanError):
        validate_plan(plan)


def test_multiple_statements_are_rejected():
    plan = AnalysisPlan(
        question="test",
        datasets=[DatasetSpec(name="d", sql="SELECT 1; DROP TABLE project")],
        tasks=[TaskSpec(id="a", name="a", description="a", output_schema={"x": "int"})],
    )
    with pytest.raises(PlanError):
        validate_plan(plan)


def test_chart_without_dependency_is_rejected():
    plan = _plan([
        TaskSpec(id="c", name="c", kind="chart", description="c", output_schema={"path": "str"}),
    ])
    with pytest.raises(PlanError):
        validate_plan(plan)
