import json
from types import SimpleNamespace

from ppm.analyst.codegen import OpenAICompatibleCodegen
from ppm.analyst.planner import plan_question


class FakeResponse:
    status_code = 200

    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


class FakeClient:
    content = ""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, *args, **kwargs):
        return FakeResponse(self.content)


def _settings():
    return SimpleNamespace(
        require_api=lambda: None,
        llm_api_base="http://example.invalid/v1",
        llm_api_key="test",
        llm_model="test-model",
        llm_temperature=0,
    )


def test_planner_parses_openai_compatible_json(monkeypatch):
    plan_json = json.dumps({
        "question": "q",
        "assumptions": [],
        "datasets": [{
            "name": "d",
            "source": "sql",
            "sql": "SELECT 1 AS x",
            "schema": {"x": "int"},
        }],
        "tasks": [{
            "id": "t",
            "name": "t",
            "kind": "dataframe",
            "depends_on": [],
            "description": "d",
            "output_schema": {"x": "int"},
            "validation": [],
        }],
    })
    FakeClient.content = plan_json
    monkeypatch.setattr("ppm.analyst.codegen.httpx.Client", FakeClient)
    monkeypatch.setattr("ppm.analyst.planner.catalog_text", lambda *args, **kwargs: "VIEW analyst_project")
    plan = plan_question("q", _settings())
    assert plan.datasets[0].name == "d"
    assert plan.tasks[0].id == "t"


def test_codegen_strips_fences(monkeypatch):
    FakeClient.content = "```python\nimport pandas as pd\n\ndef run(inputs):\n    return inputs['d']\n```"
    monkeypatch.setattr("ppm.analyst.codegen.httpx.Client", FakeClient)
    codegen = OpenAICompatibleCodegen(_settings())
    task = SimpleNamespace(
        id="t", name="t", kind="dataframe", description="d",
        output_schema={"x": "int"},
    )
    code = codegen.generate(task, {"d": ["x"]}, {"d": [{"x": 1}]})
    assert code.startswith("import pandas as pd")
    assert "def run" in code
