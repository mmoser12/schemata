"""Suggest a target schema from a pile of files.

For every column in every file we work out (name, unit, kind), then group
columns that mean the same thing across files:

  1. known lab concepts (LIBRARY) matched by synonym or fuzzy name
  2. otherwise identical normalized names ("Flow Rate" == "flow_rate")
  3. otherwise near-identical names (fuzzy, high cutoff)

Each group becomes one suggested target with a kind, a canonical unit and the
header spellings seen as synonyms. The user reviews it before it is saved.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from . import dates, units
from .headers import match_header, split_header

# Common lab quantities. "unit" is the canonical unit; "number" kinds are unitless.
LIBRARY: dict[str, dict] = {
    "sample_id": {"kind": "id", "synonyms": ["sample id", "sampleid", "sample", "id", "sample no",
                                              "sample number", "specimen", "sample name"]},
    "timestamp": {"kind": "datetime", "synonyms": ["timestamp", "date", "datum", "time", "datetime", "date time", "collected",
                                                    "collection date", "measured at", "measurement time"]},
    "temperature": {"kind": "quantity", "unit": "degC", "synonyms": ["temperature", "temp", "t"]},
    "mass": {"kind": "quantity", "unit": "g", "synonyms": ["mass", "weight", "wt", "sample mass"]},
    "pressure": {"kind": "quantity", "unit": "kPa", "synonyms": ["pressure", "p", "press", "pres"]},
    "volume": {"kind": "quantity", "unit": "mL", "synonyms": ["volume", "vol", "v"]},
    "concentration": {"kind": "quantity", "unit": "mM", "synonyms": ["concentration", "conc", "c"]},
    "length": {"kind": "quantity", "unit": "mm", "synonyms": ["length", "len", "distance", "width",
                                                               "height", "diameter"]},
    "wavelength": {"kind": "quantity", "unit": "nm", "synonyms": ["wavelength", "lambda", "wl"]},
    "elapsed_time": {"kind": "quantity", "unit": "s", "synonyms": ["elapsed", "elapsed time", "duration",
                                                                    "time elapsed", "run time"]},
    "flow_rate": {"kind": "quantity", "unit": "mL/min", "synonyms": ["flow", "flow rate", "flowrate"]},
    "voltage": {"kind": "quantity", "unit": "V", "synonyms": ["voltage", "potential"]},
    "current": {"kind": "quantity", "unit": "mA", "synonyms": ["current"]},
    "ph": {"kind": "number", "synonyms": ["ph"], "valid_range": (0.0, 14.0)},
    "absorbance": {"kind": "number", "synonyms": ["absorbance", "abs", "od", "optical density"]},
    "od600": {"kind": "number", "synonyms": ["od600", "od 600", "a600"]},
    "humidity": {"kind": "quantity", "unit": "percent", "synonyms": ["humidity", "rh", "relative humidity"]},
    "operator": {"kind": "text", "synonyms": ["operator", "analyst", "technician", "user", "measured by"]},
    "notes": {"kind": "text", "synonyms": ["notes", "note", "comment", "comments", "remarks"]},
}


@dataclass
class _Column:
    file: str
    raw: str
    name: str
    unit: Optional[str]
    kind: str  # id | datetime | numeric | text


@dataclass
class _Group:
    key: str
    concept: Optional[str]
    columns: list[_Column] = field(default_factory=list)


def _snake(name: str) -> str:
    s = re.sub(r"[^0-9a-z]+", "_", name.lower()).strip("_")
    if not s or not s[0].isalpha():
        s = "col_" + s
    return s[:48]


def _column_kind(s: pd.Series, name: str, position: int) -> str:
    values = s.dropna()
    if values.empty:
        return "text"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "datetime"
    nums = pd.to_numeric(values, errors="coerce")
    if nums.notna().mean() >= 0.95:
        return "numeric"
    g = dates.detect_format(s)
    if g.format not in (None, "mixed") or (g.format == "mixed" and g.n_failed == 0):
        return "datetime"
    looks_like_id = values.astype(str).nunique() == len(values) and (
        position == 0 or re.search(r"\b(id|sample|name|code|no)\b", name) is not None)
    return "id" if looks_like_id else "text"


def _concept_for(name: str) -> Optional[str]:
    hm = match_header(name, LIBRARY, fuzzy_cutoff=0.82)
    return hm.target


def suggest_targets(tables: dict[str, pd.DataFrame]) -> tuple[dict, list[dict]]:
    """Return (targets, notes). notes explains where each suggested column came from."""
    columns: list[_Column] = []
    for fname, df in tables.items():
        for i, col in enumerate(df.columns):
            name, token = split_header(col)
            if not name:
                continue
            columns.append(_Column(fname, str(col), name, units.token_to_unit(token),
                                   _column_kind(df[col], name, i)))

    groups: list[_Group] = []
    by_key: dict[str, _Group] = {}
    for c in columns:
        concept = _concept_for(c.name)
        if concept is not None:
            spec = LIBRARY[concept]
            # A concept only applies if the data agrees with it (e.g. "t" that is text isn't temperature).
            fits = {"id": c.kind in ("id", "text"), "datetime": c.kind == "datetime",
                    "text": c.kind in ("text", "id"), "number": c.kind == "numeric",
                    "quantity": c.kind == "numeric"}[spec["kind"]]
            if spec["kind"] == "quantity" and c.unit and not units.same_dimension(c.unit, units.parse_unit(spec["unit"])):
                fits = False
            if not fits:
                concept = None
        key = concept or _snake(c.name)
        if key not in by_key and concept is None:
            close = difflib.get_close_matches(key, [g.key for g in groups if g.concept is None], n=1, cutoff=0.88)
            if close:
                key = close[0]
        if key not in by_key:
            by_key[key] = _Group(key, concept)
            groups.append(by_key[key])
        by_key[key].columns.append(c)

    # The id and datetime columns lead, then everything in first-seen order.
    order = {"id": 0, "datetime": 1}
    groups.sort(key=lambda g: order.get(_group_kind(g), 2))

    targets: dict[str, dict] = {}
    notes: list[dict] = []
    for g in groups:
        kind = _group_kind(g)
        spec: dict = {"kind": kind}
        if kind == "quantity":
            spec["unit"] = _group_unit(g)
        lib = LIBRARY.get(g.concept or "", {})
        if lib.get("valid_range") and kind in ("quantity", "number"):
            spec["valid_range"] = lib["valid_range"]
        syns = {c.name for c in g.columns} | set(lib.get("synonyms", []))
        syns.discard(g.key.replace("_", " "))
        spec["synonyms"] = sorted(syns)
        targets[g.key] = spec
        files = sorted({c.file for c in g.columns})
        notes.append({"target": g.key, "kind": kind, "unit": units.pretty(spec.get("unit")) or None,
                      "seen_as": sorted({c.raw for c in g.columns}), "files": files,
                      "in_all_files": len(files) == len(tables)})
    return targets, notes


def _group_kind(g: _Group) -> str:
    if g.concept:
        return LIBRARY[g.concept]["kind"]
    kinds = [c.kind for c in g.columns]
    top = max(set(kinds), key=kinds.count)
    if top == "numeric":
        return "quantity" if any(c.unit for c in g.columns) else "number"
    return top


def _group_unit(g: _Group) -> str:
    seen = [c.unit for c in g.columns if c.unit]
    lib_unit = LIBRARY.get(g.concept or "", {}).get("unit")
    if lib_unit:
        return units.parse_unit(lib_unit)
    if seen:
        return max(set(seen), key=seen.count)
    return "dimensionless"
