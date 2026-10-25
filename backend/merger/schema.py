"""Target schema: the clean columns every input file gets mapped onto.

Each project has its own schema (edited in the UI or suggested from the files).
Each target has:
  kind         "id" | "datetime" | "quantity" | "number" | "text"
  unit         canonical unit for quantities (pint syntax); quantities get converted
  synonyms     header names that map here by rule (normalized: lowercase, words)
  valid_range  (min, max) for quantities/numbers, used for validation checks
  description  optional free text shown in the UI

"number" is for unitless numeric values (pH, absorbance, counts): no conversion,
but range checks still apply.
"""

from __future__ import annotations

import copy
import re

from . import units

KINDS = ("id", "datetime", "quantity", "number", "text")

TARGETS: dict[str, dict] = {
    "sample_id": {
        "kind": "id",
        "synonyms": ["sample id", "sampleid", "sample", "id", "sample no", "sample number", "specimen"],
    },
    "timestamp": {
        "kind": "datetime",
        "synonyms": ["timestamp", "date", "time", "datetime", "date time", "collected",
                     "collection date", "measured at", "measurement time"],
    },
    "temperature": {
        "kind": "quantity",
        "unit": "degC",
        "synonyms": ["temperature", "temp", "t"],
        "valid_range": (-50.0, 150.0),
    },
    "mass": {
        "kind": "quantity",
        "unit": "g",
        "synonyms": ["mass", "weight", "wt", "sample mass"],
        "valid_range": (0.0, 10_000.0),
    },
    "pressure": {
        "kind": "quantity",
        "unit": "kPa",
        "synonyms": ["pressure", "p", "press", "pres"],
        "valid_range": (50.0, 200.0),
    },
}

TEMPLATES: dict[str, dict] = {
    "lab": {"label": "Lab measurements", "description": "Sample ID, timestamp, temperature, mass, pressure",
            "targets": TARGETS},
    "blank": {"label": "Start empty", "description": "Upload files, then let Schemata suggest a schema",
              "targets": {}},
}

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


class SchemaError(ValueError):
    pass


def from_template(name: str) -> dict:
    if name not in TEMPLATES:
        raise SchemaError(f"unknown template '{name}'")
    return copy.deepcopy(TEMPLATES[name]["targets"])


def normalize_targets(targets: dict) -> dict:
    """Validate a user-supplied schema and return a cleaned copy. Raises SchemaError."""
    from .headers import normalize_name

    if not isinstance(targets, dict):
        raise SchemaError("schema must be an object of target columns")
    if len(targets) > 200:
        raise SchemaError("a schema can have at most 200 columns")
    out: dict[str, dict] = {}
    reserved = {"source_file"}
    for name, spec in targets.items():
        if not isinstance(name, str) or not _NAME.match(name):
            raise SchemaError(f"'{name}': column names must start with a letter and use only "
                              "lowercase letters, digits and _ (max 48 characters)")
        if name in reserved:
            raise SchemaError(f"'{name}' is reserved")
        if not isinstance(spec, dict):
            raise SchemaError(f"'{name}': settings must be an object")
        kind = spec.get("kind")
        if kind not in KINDS:
            raise SchemaError(f"'{name}': kind must be one of {', '.join(KINDS)}")
        clean: dict = {"kind": kind}

        if kind == "quantity":
            try:
                clean["unit"] = units.parse_unit(str(spec.get("unit") or ""))
            except ValueError as e:
                raise SchemaError(f"'{name}': {e}")

        syns = spec.get("synonyms") or []
        if isinstance(syns, str):
            syns = syns.split(",")
        clean["synonyms"] = sorted({normalize_name(s) for s in syns if normalize_name(str(s))})

        rng = spec.get("valid_range")
        if rng not in (None, [], ()) and kind in ("quantity", "number"):
            try:
                lo, hi = (None if v in (None, "") else float(v) for v in rng)
            except (TypeError, ValueError):
                raise SchemaError(f"'{name}': valid range must be two numbers")
            if lo is not None and hi is not None:
                if lo > hi:
                    raise SchemaError(f"'{name}': valid range minimum is larger than the maximum")
                clean["valid_range"] = (lo, hi)
            elif lo is not None or hi is not None:
                clean["valid_range"] = (lo if lo is not None else float("-inf"),
                                        hi if hi is not None else float("inf"))

        if spec.get("description"):
            clean["description"] = str(spec["description"])[:300]
        out[name] = clean
    return out


def to_json(targets: dict) -> dict:
    """JSON-safe copy (tuples -> lists, infinities -> None)."""
    out = {}
    for name, spec in targets.items():
        s = dict(spec)
        if "valid_range" in s:
            s["valid_range"] = [None if abs(v) == float("inf") else v for v in s["valid_range"]]
        out[name] = s
    return out


def from_json(targets: dict) -> dict:
    out = {}
    for name, spec in targets.items():
        s = dict(spec)
        if s.get("valid_range") is not None:
            lo, hi = s["valid_range"]
            s["valid_range"] = (float("-inf") if lo is None else lo, float("inf") if hi is None else hi)
        out[name] = s
    return out
