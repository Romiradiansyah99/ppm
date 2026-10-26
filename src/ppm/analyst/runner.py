"""Subprocess runner for one generated task.

Executed as: python -I runner.py <code.py> <spec.json> <out_dir>
No database credentials or API keys are present in the environment.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib

matplotlib.use("Agg")
import pandas as pd

import ppm_runtime


def _frame_info(frame: pd.DataFrame, path: Path) -> dict:
    return {
        "kind": "dataframe",
        "path": str(path),
        "rows": int(len(frame)),
        "columns": [str(column) for column in frame.columns],
        "dtypes": {str(column): str(dtype) for column, dtype in frame.dtypes.items()},
        "head": json.loads(frame.head(5).to_json(orient="records", date_format="iso")),
    }


def main() -> int:
    code_path = Path(sys.argv[1]).resolve()
    spec_path = Path(sys.argv[2]).resolve()
    out_dir = Path(sys.argv[3]).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    inputs = {
        name: pd.read_parquet(Path(relative))
        for name, relative in spec.get("inputs", {}).items()
    }

    spec_obj = importlib.util.spec_from_file_location("analyst_task", code_path)
    module = importlib.util.module_from_spec(spec_obj)
    spec_obj.loader.exec_module(module)

    result = module.run(inputs)
    if isinstance(result, pd.DataFrame):
        output_path = out_dir / "result.parquet"
        result.to_parquet(output_path, index=False)
        info = _frame_info(result, output_path)
    elif isinstance(result, (str, Path)):
        path = Path(result)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.is_file():
            raise FileNotFoundError(f"task returned a path that does not exist: {path}")
        info = {"kind": "file", "path": str(path), "bytes": path.stat().st_size}
    elif isinstance(result, dict) and result.get("kind") == "file":
        path = Path(result["path"])
        if not path.is_absolute():
            path = Path.cwd() / path
        info = {"kind": "file", "path": str(path), "bytes": path.stat().st_size}
    else:
        raise TypeError(f"run() returned unsupported type: {type(result)!r}")

    (out_dir / "result.json").write_text(json.dumps(info), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        error = traceback.format_exc()
        out_dir = Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else Path(".")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "error.txt").write_text(error, encoding="utf-8")
        print(error, file=sys.stderr)
        raise SystemExit(1)
