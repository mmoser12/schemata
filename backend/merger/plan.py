"""Build a reviewable merge plan, then apply it.

The plan is plain data (to_dict / from_dict) so the frontend can show it.

User edits are *overrides* layered on top of the automatic plan, keyed by file
and source column:

    {"lab_alice.csv": {"Temp (C)": {"target": "temperature", "unit": "degF"}}}

  target       a target name, or None to drop the column on purpose
  unit         the unit the source values are in (quantities)
  date_format  strftime format, "ISO8601" or "mixed" (datetimes)

Because the plan is always rebuilt from the data plus the overrides, adding a
file, changing a file or editing the schema never loses a reviewed decision.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

import pandas as pd

from . import dates, units
from .headers import HeaderMatch, LLMMatcher, match_header, split_header
from .schema import TARGETS


@dataclass
class ColumnMapping:
    source: str
    target: Optional[str]
    confidence: float
    method: str                        # rule | fuzzy | llm | user
    unit: Optional[str] = None         # pint unit of the source values (quantities)
    unit_source: Optional[str] = None  # header | inferred | default | user
    unit_note: str = ""
    date_format: Optional[str] = None  # for datetime targets
    date_note: str = ""


@dataclass
class FilePlan:
    file: str
    mappings: list[ColumnMapping] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    read_options: dict = field(default_factory=dict)

    def mapping_for(self, target: str) -> Optional[ColumnMapping]:
        return next((m for m in self.mappings if m.target == target), None)


@dataclass
class Issue:
    severity: str  # error | warning | info
    file: Optional[str]
    column: Optional[str]
    message: str
    kind: str = "general"  # unit_conflict | date | mapping | validation | drift | general


@dataclass
class MergePlan:
    targets: dict
    files: list[FilePlan]
    issues: list[Issue]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MergePlan":
        return cls(
            targets=d["targets"],
            files=[FilePlan(file=f["file"], mappings=[ColumnMapping(**m) for m in f["mappings"]],
                            unmapped=list(f.get("unmapped", [])),
                            read_options=dict(f.get("read_options") or {})) for f in d["files"]],
            issues=[Issue(**i) for i in d.get("issues", [])],
        )


def _priority(m: ColumnMapping) -> tuple[bool, float]:
    return (m.method == "user", m.confidence)


def build_plan(tables: dict[str, pd.DataFrame], targets: dict = TARGETS,
               llm_matcher: Optional[LLMMatcher] = None, overrides: Optional[dict] = None,
               read_options: Optional[dict] = None) -> MergePlan:
    issues: list[Issue] = []
    ambiguous: list[tuple[ColumnMapping, str, Issue]] = []  # (mapping, alternative format, its warning)
    file_plans: list[FilePlan] = []
    overrides = overrides or {}
    read_options = read_options or {}

    # Pass 1: match headers, read units from headers, detect date formats.
    for fname, df in tables.items():
        fp = FilePlan(file=fname, read_options=dict(read_options.get(fname) or {}))
        file_overrides = overrides.get(fname) or {}
        for col in df.columns:
            name, unit_token = split_header(col)
            ov = file_overrides.get(str(col)) or {}
            if "target" in ov and (ov["target"] is None or ov["target"] in targets):
                if ov["target"] is None:
                    fp.unmapped.append(str(col))
                    continue
                hm = HeaderMatch(ov["target"], 1.0, "user")
            else:
                hm = match_header(name, targets, llm_matcher)
            if hm.target is None:
                fp.unmapped.append(str(col))
                continue

            m = ColumnMapping(source=str(col), target=hm.target, confidence=hm.confidence, method=hm.method)
            existing = fp.mapping_for(hm.target)
            if existing is not None:
                # Two columns want the same target: a user choice wins, then confidence.
                if _priority(m) > _priority(existing):
                    fp.mappings.remove(existing)
                    kept, dropped = str(col), existing.source
                else:
                    kept, dropped = existing.source, str(col)
                fp.unmapped.append(dropped)
                issues.append(Issue("warning", fname, dropped,
                                    f"also looks like '{hm.target}'; kept '{kept}' instead", "mapping"))
                if dropped == str(col):
                    continue

            spec = targets[hm.target]
            if spec["kind"] == "quantity":
                if ov.get("unit"):
                    try:
                        u = units.parse_unit(ov["unit"])
                        if units.same_dimension(u, spec["unit"]):
                            m.unit, m.unit_source, m.unit_note = u, "user", "set by you"
                        else:
                            issues.append(Issue("error", fname, str(col),
                                                f"'{ov['unit']}' is not a {units.dimension_name(spec['unit'])} "
                                                f"unit, so it can't be converted to {units.pretty(spec['unit'])}",
                                                "unit_conflict"))
                    except ValueError as e:
                        issues.append(Issue("error", fname, str(col), str(e), "unit_conflict"))
                if m.unit is None:
                    unit = units.token_to_unit(unit_token)
                    if unit is not None and not units.same_dimension(unit, spec["unit"]):
                        issues.append(Issue("error", fname, str(col),
                                            f"header unit '{unit_token}' is not a "
                                            f"{units.dimension_name(spec['unit'])} unit", "unit_conflict"))
                        unit = None
                    if unit is not None:
                        m.unit, m.unit_source = unit, "header"
                        m.unit_note = f"read '{unit_token}' from header"

            elif spec["kind"] == "datetime":
                if ov.get("date_format"):
                    m.date_format, m.date_note = ov["date_format"], "set by you"
                    failed = int(dates.parse(df[col], m.date_format).isna().sum() - df[col].isna().sum())
                    if failed > 0:
                        issues.append(Issue("warning", fname, str(col),
                                            f"{failed} value(s) don't match the format you chose", "date"))
                else:
                    g = dates.detect_format(df[col])
                    m.date_format, m.date_note = g.format, g.note
                    if g.ambiguous_with or g.n_failed:
                        issue = Issue("warning", fname, str(col), g.note, "date")
                        issues.append(issue)
                        if g.ambiguous_with and not g.n_failed:
                            ambiguous.append((m, g.ambiguous_with, issue))

            if hm.method not in ("rule", "user"):
                issues.append(Issue("info", fname, str(col),
                                    f"matched to '{hm.target}' by {hm.method} (confidence {hm.confidence})",
                                    "mapping"))
            fp.mappings.append(m)
        file_plans.append(fp)

    # Ambiguous dates (every day <= 12): follow a file whose format is unambiguous.
    if ambiguous:
        unsure = {id(m) for m, _, _ in ambiguous}
        settled = {m.date_format for fp in file_plans for m in fp.mappings
                   if m.date_format and id(m) not in unsure}
        for m, alt, issue in ambiguous:
            best = m.date_format
            choice = best if best in settled else alt if alt in settled else None
            if choice is None:
                continue
            other = alt if choice == best else best
            m.date_format = choice
            m.date_note = (f"could be {dates.human(choice)} or {dates.human(other)}; chose {dates.human(choice)} "
                           "because the other files use it")
            issue.severity, issue.message = "info", m.date_note

    # Pass 2: infer missing units by comparing against files that are labeled.
    for target, spec in targets.items():
        if spec["kind"] != "quantity":
            continue
        labeled_medians = []
        for fp in file_plans:
            m = fp.mapping_for(target)
            if m and m.unit:
                med = units.finite_median(units.convert(tables[fp.file][m.source], m.unit, spec["unit"]))
                if med is not None:
                    labeled_medians.append((fp.file, m.source, med))
        reference = float(pd.Series([x[2] for x in labeled_medians]).median()) if labeled_medians else None

        # Labeled files that disagree with the rest (possible mislabeled header).
        if reference is not None and len(labeled_medians) >= 3:
            for fname, col, med in labeled_medians:
                if abs(med - reference) / max(abs(reference), 1e-9) > 0.5:
                    issues.append(Issue("warning", fname, col,
                                        f"converted median {med:.4g} {units.pretty(spec['unit'])} is far from other "
                                        f"files (~{reference:.4g}); is the header unit right?", "unit_conflict"))

        for fp in file_plans:
            m = fp.mapping_for(target)
            if m is None or m.unit is not None:
                continue
            g = units.infer_from_consensus(tables[fp.file][m.source], spec["unit"], reference)
            m.unit, m.unit_source, m.unit_note = g.unit, g.source, g.note
            if g.source == "inferred" and not units.same_unit(g.unit, spec["unit"]):
                issues.append(Issue("warning", fp.file, m.source,
                                    f"unit conflict: {g.note}. Converting from {units.pretty(g.unit)}.",
                                    "unit_conflict"))
            elif g.source == "inferred":
                issues.append(Issue("info", fp.file, m.source, g.note, "unit_conflict"))
            elif g.source == "default":
                issues.append(Issue("warning", fp.file, m.source,
                                    f"no unit found; assuming {units.pretty(spec['unit'])}", "unit_conflict"))

    for fp in file_plans:
        for target in targets:
            if fp.mapping_for(target) is None:
                issues.append(Issue("info", fp.file, None, f"no column found for '{target}'", "mapping"))
        for col in fp.unmapped:
            issues.append(Issue("info", fp.file, col, "column not mapped to any target; it will be dropped",
                                "mapping"))

    return MergePlan(targets=targets, files=file_plans, issues=issues)


def _clean_column(col: pd.Series, mapping: ColumnMapping, spec: dict) -> pd.Series:
    if spec["kind"] == "quantity":
        return units.convert(col, mapping.unit or spec["unit"], spec["unit"])
    if spec["kind"] == "number":
        return pd.to_numeric(col, errors="coerce")
    if spec["kind"] == "datetime":
        return dates.parse(col, mapping.date_format)
    return col.astype("string").str.strip()


def apply_plan(tables: dict[str, pd.DataFrame], plan: MergePlan) -> tuple[pd.DataFrame, list[Issue]]:
    """Return the merged clean table and validation issues."""
    targets = plan.targets
    frames = []
    for fp in plan.files:
        src = tables[fp.file]
        out = pd.DataFrame(index=src.index)
        out["source_file"] = fp.file
        for target, spec in targets.items():
            m = fp.mapping_for(target)
            out[target] = pd.NA if m is None else _clean_column(src[m.source], m, spec)
        frames.append(out)

    merged = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["source_file", *targets])
    return merged, validate(merged, targets)


def column_previews(tables: dict[str, pd.DataFrame], plan: MergePlan, n: int = 3) -> dict:
    """{file: {source column: [{"raw": ..., "clean": ...}]}}: what a mapping does to real values."""
    out: dict[str, dict] = {}
    for fp in plan.files:
        src = tables[fp.file]
        per_file = {}
        for m in fp.mappings:
            spec = plan.targets.get(m.target)
            if spec is None:
                continue
            raw = src[m.source].dropna().head(n)
            clean = _clean_column(raw, m, spec)
            pairs = []
            for r, c in zip(raw.tolist(), clean.tolist()):
                if pd.isna(c):
                    shown = "(unreadable)"
                elif isinstance(c, pd.Timestamp):
                    shown = c.strftime("%Y-%m-%d %H:%M") if (c.hour or c.minute) else c.strftime("%Y-%m-%d")
                elif isinstance(c, float):
                    shown = f"{c:.4g}"
                else:
                    shown = str(c)
                pairs.append({"raw": str(r), "clean": shown})
            per_file[m.source] = pairs
        out[fp.file] = per_file
    return out


def validate(df: pd.DataFrame, targets: dict) -> list[Issue]:
    issues: list[Issue] = []
    for target, spec in targets.items():
        if target not in df:
            continue
        s = df[target]
        if spec["kind"] == "id":
            if s.isna().any():
                issues.append(Issue("error", None, target, f"{int(s.isna().sum())} row(s) missing {target}",
                                    "validation"))
            dup = s.dropna()[s.dropna().duplicated()]
            if len(dup):
                issues.append(Issue("warning", None, target,
                                    f"duplicate {target}: {', '.join(map(str, dup.unique()[:5]))}", "validation"))
        if spec["kind"] == "datetime" and s.isna().any():
            issues.append(Issue("warning", None, target, f"{int(s.isna().sum())} value(s) could not be parsed",
                                "validation"))
        if spec["kind"] in ("quantity", "number") and spec.get("valid_range"):
            lo, hi = spec["valid_range"]
            s = pd.to_numeric(s, errors="coerce")
            bad = df[(s < lo) | (s > hi)]
            unit = f" {units.pretty(spec['unit'])}" if spec.get("unit") else ""
            for fname, grp in bad.groupby("source_file"):
                issues.append(Issue("error", fname, target,
                                    f"{len(grp)} value(s) outside {lo:g}..{hi:g}{unit}", "validation"))
    return issues
