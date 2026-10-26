import pytest

from ppm.analyst.static_analysis import CodeRejected, check_code


def test_allowed_code_passes():
    code = """
import pandas as pd
import ppm_runtime

def run(inputs: dict):
    return inputs["x"].copy()
"""
    assert check_code(code) == code


def test_forbidden_import_is_rejected():
    with pytest.raises(CodeRejected, match="import not allowed"):
        check_code("import os\n\ndef run(inputs):\n    return inputs\n")


def test_forbidden_call_is_rejected():
    with pytest.raises(CodeRejected, match="call not allowed"):
        check_code("def run(inputs):\n    return open('secret.txt')\n")


def test_dunder_attribute_is_rejected():
    with pytest.raises(CodeRejected, match="attribute not allowed"):
        check_code("def run(inputs):\n    return inputs.__class__\n")


def test_missing_run_is_rejected():
    with pytest.raises(CodeRejected, match="def run"):
        check_code("x = 1\n")
