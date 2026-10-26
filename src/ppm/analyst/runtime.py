"""Sandboxed execution of generated task code.

Each run gets its own directory. Inputs are parquet files; the subprocess gets a
minimal environment (no PPM_DSN, no API key) and a hard timeout. This is a
containment layer, not a full security boundary; it is documented as such.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ppm.config import REPO_ROOT


@dataclass
class ExecutionResult:
    status: str
    kind: str | None = None
    output_path: str | None = None
    output_hash: str | None = None
    rows: int | None = None
    columns: list[str] = field(default_factory=list)
    duration_ms: int = 0
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


def hash_bytes(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def cache_key(code: str, inputs: dict[str, dict]) -> str:
    payload = {
        "code": code,
        "inputs": {name: info.get("hash") for name, info in sorted(inputs.items())},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def prepare_run_dir(run_id: str) -> Path:
    run_dir = REPO_ROOT / "exports" / "analyst" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in ("inputs", "artifacts"):
        (run_dir / name).mkdir(exist_ok=True)
    (run_dir / ".mplconfig").mkdir(exist_ok=True)
    shutil.copy2(Path(__file__).with_name("runner.py"), run_dir / "runner.py")
    shutil.copy2(Path(__file__).with_name("runtime_helpers.py"), run_dir / "ppm_runtime.py")
    return run_dir


def write_inputs(run_dir: Path, frames: dict[str, pd.DataFrame]) -> dict[str, dict]:
    inputs: dict[str, dict] = {}
    for name, frame in frames.items():
        path = run_dir / "inputs" / f"{name}.parquet"
        frame.to_parquet(path, index=False)
        inputs[name] = {
            "path": f"inputs/{name}.parquet",
            "hash": hash_bytes(path),
            "rows": int(len(frame)),
            "columns": [str(column) for column in frame.columns],
        }
    return inputs


def _minimal_env(run_dir: Path) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "WINDIR": os.environ.get("WINDIR", ""),
        "TEMP": os.environ.get("TEMP", ""),
        "TMP": os.environ.get("TMP", ""),
        "PYTHONIOENCODING": "utf-8",
        "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": str(run_dir / ".mplconfig"),
    }
    return {key: value for key, value in env.items() if value}


def execute_task(
    run_dir: Path,
    task_key: str,
    code: str,
    inputs: dict[str, dict],
    timeout: int = 120,
) -> ExecutionResult:
    code_path = run_dir / f"task_{task_key}.py"
    spec_path = run_dir / f"task_{task_key}.json"
    out_dir = run_dir / f"out_{task_key}"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    code_path.write_text(code, encoding="utf-8")
    spec_path.write_text(
        json.dumps({"inputs": {name: info["path"] for name, info in inputs.items()}}),
        encoding="utf-8",
    )

    command = [
        sys.executable,
        "-I",
        str(run_dir / "runner.py"),
        str(code_path),
        str(spec_path),
        str(out_dir),
    ]
    started = time.perf_counter()
    try:
        process = subprocess.run(
            command,
            cwd=run_dir,
            env=_minimal_env(run_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ExecutionResult(
            status="failed",
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=f"task timed out after {timeout}s",
        )

    duration_ms = int((time.perf_counter() - started) * 1000)
    if process.returncode != 0:
        error_path = out_dir / "error.txt"
        error = error_path.read_text(encoding="utf-8") if error_path.exists() else process.stderr
        return ExecutionResult(
            status="failed",
            duration_ms=duration_ms,
            stdout=process.stdout,
            stderr=process.stderr,
            error=error[-4000:],
        )

    result_path = out_dir / "result.json"
    if not result_path.exists():
        return ExecutionResult(
            status="failed",
            duration_ms=duration_ms,
            stdout=process.stdout,
            stderr=process.stderr,
            error="runner produced no result.json",
        )
    info = json.loads(result_path.read_text(encoding="utf-8"))
    output_path = info["path"]
    return ExecutionResult(
        status="ok",
        kind=info.get("kind"),
        output_path=output_path,
        output_hash=hash_bytes(output_path),
        rows=info.get("rows"),
        columns=info.get("columns", []),
        duration_ms=duration_ms,
        stdout=process.stdout,
        stderr=process.stderr,
    )
