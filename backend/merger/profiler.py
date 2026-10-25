"""Column profiling."""

from __future__ import annotations

import pandas as pd

from .readers import read_table  # noqa: F401  (re-exported for callers of the old API)


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
    if inferred == "numeric" and nums.notna().any():
        out.update(min=float(nums.min()), max=float(nums.max()), median=float(nums.median()))
    return out


def profile_file(df: pd.DataFrame) -> dict:
    return {
        "n_rows": int(len(df)),
        "columns": {str(c): profile_column(df[c]) for c in df.columns},
    }


def head_records(df: pd.DataFrame, n: int = 20) -> dict:
    """First rows as JSON-safe strings, for showing a raw file as it was read."""
    head = df.head(n)
    return {
        "columns": [str(c) for c in head.columns],
        "rows": [["" if pd.isna(v) else str(v) for v in row] for row in head.itertuples(index=False)],
    }
