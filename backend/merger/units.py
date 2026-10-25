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

import re
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
import pint

ureg = pint.UnitRegistry()
Q_ = ureg.Quantity

# Tokens where case carries meaning (M = molar, m = metre; mS vs ms).
CASE_SENSITIVE_TOKENS: dict[str, str] = {
    "M": "molar", "mM": "millimolar", "uM": "micromolar", "µM": "micromolar", "μM": "micromolar",
    "nM": "nanomolar", "pM": "picomolar",
    "mS": "millisiemens", "uS": "microsiemens", "µS": "microsiemens",
}

# Header tokens -> pint unit strings. Keys are lowercase with spaces removed.
UNIT_TOKENS: dict[str, str] = {
    # temperature
    "c": "degC", "°c": "degC", "ºc": "degC", "degc": "degC", "celsius": "degC", "degreesc": "degC",
    "f": "degF", "°f": "degF", "ºf": "degF", "degf": "degF", "fahrenheit": "degF", "degreesf": "degF",
    "k": "kelvin", "kelvin": "kelvin",
    # mass
    "ng": "nanogram", "ug": "microgram", "µg": "microgram", "μg": "microgram", "mcg": "microgram",
    "mg": "milligram", "g": "gram", "gram": "gram", "grams": "gram", "kg": "kilogram",
    "lb": "pound", "lbs": "pound", "oz": "ounce",
    # pressure
    "pa": "pascal", "hpa": "hectopascal", "kpa": "kilopascal", "mpa": "megapascal",
    "atm": "atmosphere", "bar": "bar", "mbar": "millibar",
    "psi": "psi", "torr": "torr", "mmhg": "mmHg",
    # volume
    "l": "liter", "liter": "liter", "litre": "liter", "ml": "milliliter", "dl": "deciliter",
    "ul": "microliter", "µl": "microliter", "μl": "microliter", "nl": "nanoliter",
    # length
    "m": "meter", "km": "kilometer", "cm": "centimeter", "mm": "millimeter",
    "um": "micrometer", "µm": "micrometer", "μm": "micrometer", "nm": "nanometer", "inch": "inch",
    # time (durations; clock times are datetime columns)
    "s": "second", "sec": "second", "secs": "second", "seconds": "second", "ms": "millisecond",
    "min": "minute", "mins": "minute", "minutes": "minute",
    "h": "hour", "hr": "hour", "hrs": "hour", "hours": "hour", "day": "day", "days": "day",
    # amount of substance
    "mol": "mole", "mmol": "millimole", "umol": "micromole", "µmol": "micromole", "nmol": "nanomole",
    # energy, electrical, frequency, misc
    "j": "joule", "kj": "kilojoule", "cal": "calorie", "kcal": "kilocalorie",
    "v": "volt", "mv": "millivolt", "ma": "milliampere",
    "hz": "hertz", "khz": "kilohertz", "mhz": "megahertz",
    "rpm": "revolutions_per_minute",
    "%": "percent", "percent": "percent", "pct": "percent",
}

# Candidates to try when a column has no unit in its header, keyed by dimension.
_CANDIDATE_LISTS = [
    ["degC", "degF", "kelvin"],
    ["gram", "milligram", "kilogram", "microgram"],
    ["kilopascal", "pascal", "atmosphere", "bar", "psi"],
    ["milliliter", "liter", "microliter"],
    ["millimeter", "centimeter", "meter", "micrometer", "nanometer"],
    ["second", "minute", "hour"],
    ["millimolar", "molar", "micromolar", "nanomolar"],
]
CANDIDATES: dict[str, list[str]] = {
    str(ureg.Unit(units[0]).dimensionality): units for units in _CANDIDATE_LISTS
}

# Common units offered in the UI, grouped by what they measure.
UNIT_GROUPS: list[tuple[str, list[str]]] = [
    ("Temperature", ["degC", "degF", "kelvin"]),
    ("Mass", ["microgram", "milligram", "gram", "kilogram"]),
    ("Volume", ["microliter", "milliliter", "liter"]),
    ("Length", ["nanometer", "micrometer", "millimeter", "centimeter", "meter"]),
    ("Time (duration)", ["millisecond", "second", "minute", "hour", "day"]),
    ("Pressure", ["pascal", "kilopascal", "bar", "atmosphere", "psi", "torr"]),
    ("Concentration", ["nanomolar", "micromolar", "millimolar", "molar"]),
    ("Mass concentration", ["microgram / milliliter", "milligram / milliliter", "gram / liter"]),
    ("Amount", ["nanomole", "micromole", "millimole", "mole"]),
    ("Flow rate", ["microliter / minute", "milliliter / minute", "liter / hour"]),
    ("Electrical", ["millivolt", "volt", "milliampere", "microsiemens", "millisiemens"]),
    ("Other", ["percent", "hertz", "revolutions_per_minute", "joule", "kilojoule"]),
]


@dataclass
class UnitGuess:
    unit: Optional[str]
    source: str  # "header" | "inferred" | "default" | "none"
    note: str = ""


def _compound(token: str) -> bool:
    """Only hand compound tokens ("mg/mL", "m^2") to pint's parser; bare short
    tokens like "A" or "au" would parse as ampere / astronomical unit."""
    return bool(re.search(r"[/^*·]|\d", token))


def token_to_unit(token: Optional[str]) -> Optional[str]:
    if token is None:
        return None
    token = token.replace(" ", "")
    if token in CASE_SENSITIVE_TOKENS:
        return CASE_SENSITIVE_TOKENS[token]
    if token.lower() in UNIT_TOKENS:
        return UNIT_TOKENS[token.lower()]
    if _compound(token):
        try:
            return str(ureg.parse_units(token.replace("µ", "u").replace("μ", "u")))
        except Exception:  # pint raises several unrelated types for junk input
            return None
    return None


def parse_unit(text: str) -> str:
    """Turn user text ("mg/mL", "°C", "kelvin") into a pint unit string, or raise ValueError."""
    text = (text or "").strip()
    if not text:
        raise ValueError("unit is empty")
    known = token_to_unit(text)
    if known:
        return known
    try:
        return str(ureg.parse_units(text.replace("µ", "u").replace("μ", "u")))
    except Exception:
        raise ValueError(f"'{text}' is not a unit Schemata recognizes")


def pretty(unit: Optional[str]) -> str:
    """Short display form: 'degC' -> '°C', 'milligram / milliliter' -> 'mg/mL'."""
    if not unit:
        return ""
    try:
        text = f"{ureg.Unit(unit):~P}" or unit
    except Exception:
        return unit
    # pint writes litres as "l"; labs write "L" (mL, µL, g/L).
    return re.sub(r"(?<![A-Za-z])([munpdcμµk]?)l(?![A-Za-z])", r"\1L", text)


_DIMENSION_NAMES = {
    str(ureg.Unit(u).dimensionality): name for u, name in [
        ("degC", "temperature"), ("gram", "mass"), ("meter", "length"), ("second", "time"),
        ("liter", "volume"), ("pascal", "pressure"), ("molar", "concentration"),
        ("gram / liter", "mass concentration"), ("mole", "amount"), ("liter / minute", "flow rate"),
        ("volt", "voltage"), ("ampere", "current"), ("siemens", "conductance"), ("joule", "energy"),
        ("hertz", "frequency"), ("percent", "dimensionless"),
    ]
}


def dimensionality(unit: str) -> str:
    return str(ureg.Unit(unit).dimensionality)


def dimension_name(unit: str) -> str:
    d = dimensionality(unit)
    return _DIMENSION_NAMES.get(d, d)


def unit_groups() -> list[dict]:
    return [{"label": label, "units": [{"unit": u, "label": pretty(u), "dimension": dimension_name(u)}
                                       for u in units]}
            for label, units in UNIT_GROUPS]


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
            return UnitGuess("kelvin", "inferred", f"median {med:g} only makes sense in K")
        return UnitGuess(target_unit, "default", "no unit in header and no other file to compare with")

    scale = max(abs(reference_median), 1e-9)
    scored = []
    for cand in candidates:
        a, b = linear_coefficients(cand, target_unit)
        scored.append((abs(med * a + b - reference_median) / scale, cand, med * a + b))
    scored.sort()
    err, best, converted = scored[0]
    tu = pretty(target_unit)
    note = (f"no unit in header; median {med:g} read as {pretty(best)} gives {converted:.4g} {tu}, "
            f"matching other files (~{reference_median:.4g} {tu})")
    if not same_unit(best, target_unit):
        note += f"; reading it as {tu} would give {med:.4g} {tu}"
    if err > 0.5:
        note += " (weak match, please confirm)"
    return UnitGuess(best, "inferred", note)


def finite_median(values: pd.Series) -> Optional[float]:
    nums = pd.to_numeric(values, errors="coerce")
    nums = nums[np.isfinite(nums)]
    return float(nums.median()) if not nums.empty else None
