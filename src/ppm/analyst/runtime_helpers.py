"""Helpers available to generated task code as `ppm_runtime`."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ARTIFACT_DIR = Path("artifacts")
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def read_input(name: str) -> pd.DataFrame:
    return pd.read_parquet(Path("inputs") / f"{name}.parquet")


def write_artifact(df: pd.DataFrame, name: str) -> str:
    path = ARTIFACT_DIR / f"{name}.parquet"
    df.to_parquet(path, index=False)
    return str(path)


def save_chart(fig, name: str) -> str:
    path = ARTIFACT_DIR / f"{name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    return str(path)


def require_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    return df


def to_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")
