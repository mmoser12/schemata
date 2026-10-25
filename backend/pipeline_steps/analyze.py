"""analyze.py: per-file summary statistics from merged.csv.

The second pipeline step in the demo (clean -> analyze -> figures). Reads the
merged table and writes one row per source file with count/median/mean/std
for each measured quantity.

Usage: python analyze.py MERGED_CSV SUMMARY_CSV
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


def main() -> None:
    merged_csv = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("merged.csv")
    summary_csv = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("summary.csv")

    df = pd.read_csv(merged_csv)
    metrics = [c for c in ("temperature", "mass", "pressure") if c in df.columns]

    summary = df.groupby("source_file")[metrics].agg(["count", "median", "mean", "std"])
    summary.columns = ["_".join(col) for col in summary.columns]
    summary = summary.reset_index()

    summary_csv.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(summary_csv, index=False)
    print(f"Wrote summary for {len(summary)} file(s) to {summary_csv}")


if __name__ == "__main__":
    main()
