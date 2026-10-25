"""Date/time normalization.

We try a list of explicit formats and pick one that parses every value, so the
generated clean.py can use that exact format (reproducible) instead of guessing.
"""

from __future__ import annotations

import re
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
    "%m-%d-%Y %H:%M", "%m-%d-%Y", "%d-%m-%Y %H:%M", "%d-%m-%Y",
    "%Y%m%d", "%d %b %Y", "%d %B %Y",
]


# Something date-shaped: 2026-09-01, 09/01/2026, 1.9.2026, 24-Sep-2026, Sep 24, 2026, 20260901.
# Guards against pandas reading ISO time designators ("T1") or bare numbers as dates.
_DATEISH = re.compile(
    r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\d{1,2}[-\s][A-Za-z]{3,9}[-\s]\d{2,4}"
    r"|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}|^\d{8}$")


@dataclass
class DateGuess:
    format: Optional[str]  # strftime format, "ISO8601", "native", or None
    ambiguous_with: Optional[str] = None
    n_failed: int = 0
    note: str = ""


_HUMAN = {"%Y": "YYYY", "%m": "MM", "%d": "DD", "%H": "hh", "%M": "mm", "%S": "ss", "%b": "Mon", "%B": "Month"}


def human(fmt: Optional[str]) -> str:
    """'%m/%d/%Y %H:%M' -> 'MM/DD/YYYY hh:mm' (what people recognize)."""
    if not fmt:
        return "unknown"
    if fmt == "ISO8601":
        return "ISO 8601 (YYYY-MM-DDThh:mm)"
    if fmt in ("mixed", "native"):
        return {"mixed": "mixed formats", "native": "Excel dates"}[fmt]
    for k, v in _HUMAN.items():
        fmt = fmt.replace(k, v)
    return fmt


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

    dateish = values.str.contains(_DATEISH, regex=True).mean()
    if dateish < 0.8:
        return DateGuess(None, n_failed=len(values), note="values don't look like dates")

    matches = [fmt for fmt in CANDIDATE_FORMATS if _parses_all(values, fmt)]
    if not matches:
        parsed = pd.to_datetime(values, format="mixed", errors="coerce")
        failed = int(parsed.isna().sum())
        return DateGuess("mixed", n_failed=failed,
                         note=f"no single format fits; parsed with mixed inference, {failed} value(s) failed")

    best = matches[0]
    guess = DateGuess(best)
    # month/day vs day/month: both parse when every day is <= 12
    swapped = best
    for sep in "/-":
        md, dm = f"%m{sep}%d", f"%d{sep}%m"
        swapped = swapped.replace(md, "%TMP").replace(dm, md).replace("%TMP", dm)
    if swapped != best and swapped in matches:
        # Tie-break: lab measurements are usually close together in time, so
        # prefer the reading with the shorter overall span.
        def span(f: str) -> pd.Timedelta:
            parsed = pd.to_datetime(values, format=f)
            return parsed.max() - parsed.min()

        if span(swapped) < span(best):
            best, swapped = swapped, best
        guess = DateGuess(best, ambiguous_with=swapped,
                          note=f"could be {human(best)} or {human(swapped)}; chose {human(best)} because it gives "
                               "a tighter date range")
    return guess


def parse(series: pd.Series, fmt: Optional[str]) -> pd.Series:
    if fmt is None:
        return pd.Series(pd.NaT, index=series.index)
    if fmt == "native":
        return pd.to_datetime(series, errors="coerce")
    return pd.to_datetime(series.astype("string").str.strip(), format=fmt, errors="coerce")
