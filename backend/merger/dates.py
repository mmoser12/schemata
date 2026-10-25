"""Date/time normalization.

We try a list of explicit formats and pick one that parses every value, so the
generated clean.py can use that exact format (reproducible) instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

CANDIDATE_FORMATS = [
    "ISO8601",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
    "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y",
    "%d.%m.%Y %H:%M", "%d.%m.%Y",
    "%b %d, %Y", "%B %d, %Y",
    "%Y/%m/%d %H:%M", "%Y/%m/%d",
]


@dataclass
class DateGuess:
    format: Optional[str]  # strftime format, "ISO8601", "native", or None
    ambiguous_with: Optional[str] = None
    n_failed: int = 0
    note: str = ""


def _parses_all(values: pd.Series, fmt: str) -> bool:
    parsed = pd.to_datetime(values, format=fmt, errors="coerce")
    return bool(parsed.notna().all())


def detect_format(series: pd.Series) -> DateGuess:
    if pd.api.types.is_datetime64_any_dtype(series):
        return DateGuess("native", note="already a datetime column")

    values = series.dropna().astype(str).str.strip()
    values = values[values != ""]
    if values.empty:
        return DateGuess(None, note="no values")

    matches = [fmt for fmt in CANDIDATE_FORMATS if _parses_all(values, fmt)]
    if not matches:
        parsed = pd.to_datetime(values, format="mixed", errors="coerce")
        failed = int(parsed.isna().sum())
        return DateGuess("mixed", n_failed=failed,
                         note=f"no single format fits; parsed with mixed inference, {failed} value(s) failed")

    best = matches[0]
    guess = DateGuess(best)
    # month/day vs day/month: both parse when every day is <= 12
    swapped = best.replace("%m/%d", "%TMP").replace("%d/%m", "%m/%d").replace("%TMP", "%d/%m")
    if swapped != best and swapped in matches:
        # Tie-break: lab measurements are usually close together in time, so
        # prefer the reading with the shorter overall span.
        def span(f: str) -> pd.Timedelta:
            parsed = pd.to_datetime(values, format=f)
            return parsed.max() - parsed.min()

        if span(swapped) < span(best):
            best, swapped = swapped, best
        guess = DateGuess(best, ambiguous_with=swapped,
                          note=f"could be {best} or {swapped}; chose {best} because it gives a tighter date range")
    return guess


def parse(series: pd.Series, fmt: Optional[str]) -> pd.Series:
    if fmt is None:
        return pd.Series(pd.NaT, index=series.index)
    if fmt == "native":
        return pd.to_datetime(series, errors="coerce")
    return pd.to_datetime(series.astype("string").str.strip(), format=fmt, errors="coerce")
