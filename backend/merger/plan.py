"""Build a reviewable merge plan, then apply it.

The plan is plain data (to_dict / from_dict) so the frontend can show it,
let the user edit mappings or units, and send it back to be applied.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

import pandas as pd

from . import dates, units
from .headers import LLMMatcher, match_header, split_header
from .schema import TARGETS


@dataclass
class ColumnMapping:
    source: str
    target: Optional[str]
    confidence: float
    method: str
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

    def mapping_for(self, target: str) -> Optional[ColumnMapping]:
        return next((m for m in self.mappings if m.target == target), None)


@dataclass
class Issue:
    severity: str  # error | warning | info
    file: Optional[str]
    column: Optional[str]
    message: str
    kind: str = "general"  # unit_conflict | date | mapping | validation | general


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
                            unmapped=list(f.get("unmapped", []))) for f in d["files"]],
            issues=[Issue(**i) for i in d.get("issues", [])],
        )


def build_plan(tables: dict[str, pd.DataFrame], targets: dict = TARGETS,
               llm_matcher: Optional[LLMMatcher] = None) -> MergePlan:
    issues: list[Issue] = []
    file_plans: list[FilePlan] = []

    # Pass 1: match headers, read units from headers, detect date formats.
    for fname, df in tables.items():
        fp = FilePlan(file=fname)
        for col in df.columns:
            name, unit_token = split_header(col)
            hm = match_header(name, targets, llm_matcher)
            if hm.target is None:
                fp.unmapped.append(str(col))
                continue

            existing = fp.mapping_for(hm.target)
            if existing is not None:
                # Two columns want the same target: keep the more confident one.
                if hm.confidence > existing.confidence:
                    fp.mappings.remove(existing)
                    kept, dropped = str(col), existing.source
                else:
                    kept, dropped = existing.source, str(col)
                fp.unmapped.append(dropped)
                issues.append(Issue("warning", fname, dropped,
                                    f"also looks like '{hm.target}'; kept '{kept}' instead", "mapping"))
                if dropped == str(col):
                    continue

            m = ColumnMapping(source=str(col), target=hm.target, confidence=hm.confidence, method=hm.method)
            spec = targets[hm.target]

            if spec["kind"] == "quantity":
                unit = units.token_to_unit(unit_token)
                if unit is not None and not units.same_dimension(unit, spec["unit"]):
                    issues.append(Issue("error", fname, str(col),
                                        f"header unit '{unit_token}' is not a {hm.target} unit", "unit_conflict"))
                    unit = None
                if unit is not None:
                    m.unit, m.unit_source = unit, "header"
                    m.unit_note = f"read '{unit_token}' from header"

            elif spec["kind"] == "datetime":
                g = dates.detect_format(df[col])
                m.date_format, m.date_note = g.format, g.note
                if g.ambiguous_with:
                    issues.append(Issue("warning", fname, str(col), g.note, "date"))
                if g.n_failed:
                    issues.append(Issue("warning", fname, str(col), g.note, "date"))

            if hm.method != "rule":
                issues.append(Issue("info", fname, str(col),
                                    f"matched to '{hm.target}' by {hm.method} (confidence {hm.confidence})",
                                    "mapping"))
            fp.mappings.append(m)
        file_plans.append(fp)

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
                                        f"converted median {med:.4g} {spec['unit']} is far from other files "
                                        f"(~{reference:.4g}); is the header unit right?", "unit_conflict"))

        for fp in file_plans:
            m = fp.mapping_for(target)
            if m is None or m.unit is not None:
                continue
            g = units.infer_from_consensus(tables[fp.file][m.source], spec["unit"], reference)
            m.unit, m.unit_source, m.unit_note = g.unit, g.source, g.note
            if g.source == "inferred" and not units.same_unit(g.unit, spec["unit"]):
                issues.append(Issue("warning", fp.file, m.source,
                                    f"unit conflict: {g.note}. Converting from {g.unit}.", "unit_conflict"))
            elif g.source == "inferred":
                issues.append(Issue("info", fp.file, m.source, g.note, "unit_conflict"))
            elif g.source == "default":
                issues.append(Issue("warning", fp.file, m.source,
                                    f"no unit found; assuming {spec['unit']}", "unit_conflict"))

    for fp in file_plans:
        for target in targets:
            if fp.mapping_for(target) is None:
                issues.append(Issue("info", fp.file, None, f"no column found for '{target}'", "mapping"))
        for col in fp.unmapped:
            issues.append(Issue("info", fp.file, col, "column not mapped to any target; it will be dropped",
                                "mapping"))

    return MergePlan(targets=targets, files=file_plans, issues=issues)


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
            if m is None:
                out[target] = pd.NA
                continue
            col = src[m.source]
            if spec["kind"] == "quantity":
                out[target] = units.convert(col, m.unit or spec["unit"], spec["unit"])
            elif spec["kind"] == "datetime":
                out[target] = dates.parse(col, m.date_format)
            else:
                out[target] = col.astype("string").str.strip()
        frames.append(out)

    merged = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return merged, validate(merged, targets)


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
        if spec["kind"] == "quantity" and "valid_range" in spec:
            lo, hi = spec["valid_range"]
            bad = df[(s < lo) | (s > hi)]
            for fname, grp in bad.groupby("source_file"):
                issues.append(Issue("error", fname, target,
                                    f"{len(grp)} value(s) outside {lo}..{hi} {spec['unit']}", "validation"))
    return issues
