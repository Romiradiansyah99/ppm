"""AST-based guardrails for generated Python.

Generated code may only use a small allowlist of imports and must expose
`def run(inputs: dict)`. This is not a sandbox by itself; the executor also
runs the code in an isolated subprocess without database credentials.
"""

from __future__ import annotations

import ast

ALLOWED_IMPORTS = {
    "pandas",
    "numpy",
    "matplotlib",
    "matplotlib.pyplot",
    "json",
    "math",
    "statistics",
    "ppm_runtime",
}

FORBIDDEN_CALLS = {
    "eval", "exec", "compile", "__import__", "open", "input",
    "globals", "locals", "vars", "getattr", "setattr", "delattr",
    "breakpoint", "memoryview",
}

FORBIDDEN_NAMES = {"__builtins__", "__globals__", "__subclasses__", "__class__", "__dict__"}


class CodeRejected(Exception):
    pass


def check_code(code: str) -> str:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise CodeRejected(f"syntax error: {exc}") from exc

    has_run = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for module in modules:
                root = module.split(".")[0]
                if module not in ALLOWED_IMPORTS and root not in ALLOWED_IMPORTS:
                    raise CodeRejected(f"import not allowed: {module}")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                raise CodeRejected(f"call not allowed: {node.func.id}")
            if isinstance(node.func, ast.Attribute) and node.func.attr in FORBIDDEN_CALLS:
                raise CodeRejected(f"call not allowed: {node.func.attr}")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__") or node.attr in FORBIDDEN_NAMES:
                raise CodeRejected(f"attribute not allowed: {node.attr}")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise CodeRejected(f"name not allowed: {node.id}")
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            has_run = True

    if not has_run:
        raise CodeRejected("code must define def run(inputs: dict)")
    return code
