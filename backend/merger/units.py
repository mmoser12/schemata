"""Unit engine: detect units, check dimensions, and convert (pint).

Units come from two places:
  - the header ("Temp (C)", "mass_g")            -> source "header"
  - inference from values when the header is bare -> source "inferred"

Inference is done across files (see infer_from_consensus): a bare "Temp"
column is tested against each candidate unit, and the candidate whose
converted values agree with the other files wins. That is how a secretly
Fahrenheit file gets caught.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
import pint

ureg = pint.UnitRegistry()
Q_ = ureg.Quantity

# Header tokens -> pint unit strings. Keys are lowercase with spaces removed.
UNIT_TOKENS: dict[str, str] = {
    # temperature
    "c": "degC", "°c": "degC", "degc": "degC", "celsius": "degC", "degreesc": "degC",
    "f": "degF", "°f": "degF", "degf": "degF", "fahrenheit": "degF", "degreesf": "degF",
    "k": "kelvin", "kelvin": "kelvin",
    # mass
    "ug": "microgram", "µg": "microgram", "mcg": "microgram",
    "mg": "milligram", "g": "gram", "grams": "gram", "kg": "kilogram",
    "lb": "pound", "lbs": "pound", "oz": "ounce",
    # pressure
    "pa": "pascal", "kpa": "kilopascal", "mpa": "megapascal",
    "atm": "atmosphere", "bar": "bar", "mbar": "millibar",
    "psi": "psi", "torr": "torr", "mmhg": "mmHg",
}

# Candidates to try when a column has no unit in its header, keyed by dimension.
_CANDIDATE_LISTS = [
    ["degC", "degF", "kelvin"],
    ["gram", "milligram", "kilogram", "microgram"],
    ["kilopascal", "pascal", "atmosphere", "bar", "psi"],
]
CANDIDATES: dict[str, list[str]] = {
    str(ureg.Unit(units[0]).dimensionality): units for units in _CANDIDATE_LISTS
}


@dataclass
class UnitGuess:
    unit: Optional[str]
    source: str  # "header" | "inferred" | "default" | "none"
    note: str = ""


def token_to_unit(token: Optional[str]) -> Optional[str]:
    if token is None:
        return None
    return UNIT_TOKENS.get(token.lower().replace(" ", ""))


def dimensionality(unit: str) -> str:
    return str(ureg.Unit(unit).dimensionality)


def same_unit(a: Optional[str], b: Optional[str]) -> bool:
    if a is None or b is None:
        return a == b
    return ureg.Unit(a) == ureg.Unit(b)


def same_dimension(a: str, b: str) -> bool:
    return ureg.Unit(a).dimensionality == ureg.Unit(b).dimensionality


def linear_coefficients(from_unit: str, to_unit: str) -> tuple[float, float]:
    """Return (a, b) such that value_in_to = a * value_in_from + b.

    Every unit pint handles here (including offset temperature units) is affine,
    so two points pin it down. The generated clean.py uses these plain numbers,
    which keeps it free of a pint dependency.
    """
    y0 = Q_(0.0, from_unit).to(to_unit).magnitude
    y1 = Q_(1.0, from_unit).to(to_unit).magnitude
    return float(y1 - y0), float(y0)


def convert(values: pd.Series, from_unit: str, to_unit: str) -> pd.Series:
    a, b = linear_coefficients(from_unit, to_unit)
    return pd.to_numeric(values, errors="coerce") * a + b


def infer_from_consensus(values: pd.Series, target_unit: str, reference_median: Optional[float]) -> UnitGuess:
    """Guess the unit of a bare numeric column.

    reference_median is the median of the same target column in the files whose
    units ARE labeled, already converted to target_unit. If there is no reference,
    fall back to the target unit (with Kelvin as a special case for temperatures).
    """
    nums = pd.to_numeric(values, errors="coerce").dropna()
    if nums.empty:
        return UnitGuess(None, "none", "column has no numeric values")
    med = float(nums.median())
    candidates = CANDIDATES.get(dimensionality(target_unit), [target_unit])

    if reference_median is None:
        if dimensionality(target_unit) == "[temperature]" and med > 200:
            return UnitGuess("kelvin", "inferred", f"median {med:g} only makes sense in kelvin")
        return UnitGuess(target_unit, "default", "no unit in header and no other file to compare with")

    scale = max(abs(reference_median), 1e-9)
    scored = []
    for cand in candidates:
        a, b = linear_coefficients(cand, target_unit)
        scored.append((abs(med * a + b - reference_median) / scale, cand, med * a + b))
    scored.sort()
    err, best, converted = scored[0]
    note = (f"no unit in header; median {med:g} read as {best} gives {converted:.4g} {target_unit}, "
            f"matching other files (~{reference_median:.4g} {target_unit})")
    if not same_unit(best, target_unit):
        note += f"; reading it as {target_unit} would give {med:.4g} {target_unit}"
    if err > 0.5:
        note += " (weak match, please confirm)"
    return UnitGuess(best, "inferred", note)


def finite_median(values: pd.Series) -> Optional[float]:
    nums = pd.to_numeric(values, errors="coerce")
    nums = nums[np.isfinite(nums)]
    return float(nums.median()) if not nums.empty else None
