"""summary.py: per-group summary statistics from merged.csv (a built-in pipeline step).

Usage: python summary.py PARAMS_JSON MERGED_CSV SUMMARY_CSV

PARAMS_JSON:
    columns   numeric columns to summarize (default: every numeric column)
    group_by  column to group rows by (default "source_file"; "" for one overall row)

Writes one row per group with count/mean/median/std/min/max for each column.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd


def main() -> None:
    params = json.loads(Path(sys.argv[1]).read_text())
    merged = pd.read_csv(sys.argv[2])
    out = Path(sys.argv[3])

    columns = [c for c in params.get("columns") or [] if c in merged.columns]
    if not columns:
        columns = [c for c in merged.columns
                   if c != "source_file" and pd.to_numeric(merged[c], errors="coerce").notna().any()
                   and pd.to_numeric(merged[c], errors="coerce").notna().mean() > 0.5]
    numeric = merged[columns].apply(pd.to_numeric, errors="coerce")
    group_by = params.get("group_by", "source_file")

    stats = ["count", "mean", "median", "std", "min", "max"]
    if group_by and group_by in merged.columns:
        summary = numeric.groupby(merged[group_by]).agg(stats)
        summary.columns = ["_".join(col) for col in summary.columns]
        summary = summary.reset_index()
    else:
        summary = numeric.agg(stats).T.stack().to_frame().T
        summary.columns = ["_".join(col) for col in summary.columns]
        summary.insert(0, "group", "all rows")

    out.parent.mkdir(parents=True, exist_ok=True)
    summary.round(6).to_csv(out, index=False)
    print(f"Wrote {len(summary)} summary row(s) for {len(columns)} column(s) to {out}")


if __name__ == "__main__":
    main()
