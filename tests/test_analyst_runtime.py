from pathlib import Path

import pandas as pd

from ppm.analyst.runtime import execute_task, prepare_run_dir, write_inputs


def test_execute_dataframe_task(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # prepare_run_dir writes under REPO_ROOT; use a simple local copy instead.
    (run_dir / "inputs").mkdir()
    (run_dir / "artifacts").mkdir()
    import shutil

    from ppm.analyst import runtime as runtime_module

    shutil.copy2(Path(runtime_module.__file__).with_name("runner.py"), run_dir / "runner.py")
    shutil.copy2(Path(runtime_module.__file__).with_name("runtime_helpers.py"), run_dir / "ppm_runtime.py")

    frame = pd.DataFrame({"a": [1, 2, 3]})
    inputs = write_inputs(run_dir, {"x": frame})
    code = """
import pandas as pd

def run(inputs: dict):
    return inputs["x"].assign(b=lambda d: d["a"] * 2)
"""
    result = execute_task(run_dir, "t1", code, inputs, timeout=30)
    assert result.status == "ok"
    assert result.rows == 3
    assert "b" in result.columns
    output = pd.read_parquet(result.output_path)
    assert output["b"].tolist() == [2, 4, 6]


def test_execute_rejects_missing_input(tmp_path):
    run_dir = tmp_path / "run2"
    run_dir.mkdir()
    (run_dir / "inputs").mkdir()
    (run_dir / "artifacts").mkdir()
    import shutil

    from ppm.analyst import runtime as runtime_module

    shutil.copy2(Path(runtime_module.__file__).with_name("runner.py"), run_dir / "runner.py")
    shutil.copy2(Path(runtime_module.__file__).with_name("runtime_helpers.py"), run_dir / "ppm_runtime.py")

    code = "def run(inputs):\n    return inputs['missing']\n"
    result = execute_task(run_dir, "t2", code, {}, timeout=30)
    assert result.status == "failed"
    assert "missing" in (result.error or "").lower()
