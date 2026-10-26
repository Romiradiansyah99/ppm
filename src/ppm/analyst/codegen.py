"""Code generators: deterministic (demo/tests) and OpenAI-compatible (API)."""

from __future__ import annotations

import re
from typing import Protocol

import httpx

from ppm.analyst.schemas import TaskSpec
from ppm.config import Settings


class Codegen(Protocol):
    def generate(self, task: TaskSpec, input_schemas: dict[str, list[str]],
                 samples: dict[str, list[dict]]) -> str: ...


class StaticCodegen:
    def __init__(self, codes: dict[str, str] | None = None) -> None:
        self.codes = codes or {}

    def generate(self, task: TaskSpec, input_schemas: dict[str, list[str]],
                 samples: dict[str, list[dict]]) -> str:
        if task.code:
            return task.code
        if task.name in self.codes:
            return self.codes[task.name]
        raise KeyError(f"no code for task '{task.name}'")

    def repair(self, task: TaskSpec, code: str, error: str,
               input_schemas: dict[str, list[str]],
               samples: dict[str, list[dict]]) -> str:
        return code


_CODE_RULES = """You are the coding agent of PPM Analyst.

Return ONLY a complete Python file. No prose, no markdown fences, no fragments.
The file MUST contain a top-level function exactly named `run`:

    import pandas as pd

    def run(inputs: dict):
        ...
        return result

Rules:
- `inputs` maps dataset/task names to pandas DataFrames. Use only the inputs listed.
- Load data from `inputs["name"]`; never assume a variable called `df` exists.
- Allowed imports: pandas, numpy, matplotlib, matplotlib.pyplot, json, math,
  statistics, ppm_runtime.
- Never read files, databases, environment variables or the network.
- Do not use eval, exec, open, getattr, or dunder attributes.
- For a dataframe task, return a pandas DataFrame with exactly the declared columns.
- For a chart task, use matplotlib with the Agg backend and return
  `ppm_runtime.save_chart(fig, "<short name>")`.
- Prefer simple, deterministic pandas. No random numbers.
- If the task is a transformation, still define `run(inputs)` and read its input
  from `inputs`; do not return only the transformation body.
"""


class OpenAICompatibleCodegen:
    def __init__(self, settings: Settings) -> None:
        settings.require_api()
        self.settings = settings

    def _chat(self, messages: list[dict], *, json_mode: bool = False) -> str:
        base = self.settings.llm_api_base.rstrip("/")
        url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        with httpx.Client(timeout=180) as client:
            response = client.post(url, headers=headers, json=payload)
            if response.status_code == 400 and json_mode:
                payload.pop("response_format", None)
                response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return str(content).strip()

    def _clean(self, content: str) -> str:
        if content.startswith("```"):
            content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
            content = re.sub(r"\n?```$", "", content)
        return content.strip() + "\n"

    def generate(self, task: TaskSpec, input_schemas: dict[str, list[str]],
                 samples: dict[str, list[dict]]) -> str:
        prompt = (
            _CODE_RULES
            + "\nTask id: " + task.id
            + "\nTask name: " + task.name
            + "\nTask kind: " + task.kind
            + "\nDescription: " + task.description
            + "\nOutput columns: " + repr(task.output_schema)
            + "\nInputs: " + repr(input_schemas)
            + "\nSample rows: " + repr(samples)
            + "\n\nComplete Python file:"
        )
        return self._clean(self._chat([{"role": "user", "content": prompt}]))

    def repair(self, task: TaskSpec, code: str, error: str,
               input_schemas: dict[str, list[str]],
               samples: dict[str, list[dict]]) -> str:
        prompt = (
            _CODE_RULES
            + "\nYour previous file violated the contract.\n"
            + "Error: " + error + "\n"
            + "Previous file:\n" + code[:8000] + "\n\n"
            + "Task id: " + task.id
            + "\nTask name: " + task.name
            + "\nTask kind: " + task.kind
            + "\nDescription: " + task.description
            + "\nOutput columns: " + repr(task.output_schema)
            + "\nInputs: " + repr(input_schemas)
            + "\nSample rows: " + repr(samples)
            + "\n\nReturn the COMPLETE corrected Python file, not a fragment:"
        )
        return self._clean(self._chat([{"role": "user", "content": prompt}]))


def codegen_for(settings: Settings, kind: str) -> Codegen:
    if kind == "static":
        return StaticCodegen()
    if kind == "api":
        return OpenAICompatibleCodegen(settings)
    raise ValueError(f"unknown codegen '{kind}'")
