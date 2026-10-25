"""File loading and column profiling."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Union

import pandas as pd

EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}


def read_table(source: Union[str, Path, BinaryIO], filename: str | None = None) -> pd.DataFrame:
    """Read a CSV or Excel file. `filename` decides the format for file-like objects."""
    name = filename or str(source)
    if Path(name).suffix.lower() in EXCEL_SUFFIXES:
        return pd.read_excel(source)
    return pd.read_csv(source)


def profile_column(s: pd.Series) -> dict:
    nums = pd.to_numeric(s, errors="coerce")
    numeric_share = float(nums.notna().sum() / max(s.notna().sum(), 1))
    if pd.api.types.is_datetime64_any_dtype(s):
        inferred = "datetime"
    elif numeric_share >= 0.95:
        inferred = "numeric"
    else:
        inferred = "text"

    out = {
        "inferred_type": inferred,
        "null_rate": round(float(s.isna().mean()), 4),
        "n_unique": int(s.nunique(dropna=True)),
        "examples": [str(v) for v in s.dropna().head(3).tolist()],
    }
    if inferred == "numeric":
        out.update(min=float(nums.min()), max=float(nums.max()), median=float(nums.median()))
    return out


def profile_file(df: pd.DataFrame) -> dict:
    return {
        "n_rows": int(len(df)),
        "columns": {str(c): profile_column(df[c]) for c in df.columns},
    }
