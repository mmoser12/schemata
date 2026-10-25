"""figures.py: matplotlib figures from merged.csv and summary.csv.

The third pipeline step in the demo (clean -> analyze -> figures).

Usage: python figures.py MERGED_CSV SUMMARY_CSV FIGURES_DIR

Writes:
    FIGURES_DIR/temp_by_file.png   median temperature per source file (bar)
    FIGURES_DIR/timeseries.png     temperature over time, one line per file
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: no display needed to run as a pipeline step
import matplotlib.pyplot as plt
import pandas as pd


def main() -> None:
    merged_csv = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("merged.csv")
    summary_csv = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("summary.csv")
    figures_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("figures")
    figures_dir.mkdir(parents=True, exist_ok=True)

    merged = pd.read_csv(merged_csv, parse_dates=["timestamp"])
    summary = pd.read_csv(summary_csv)

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(summary["source_file"], summary["temperature_median"])
    ax.set_ylabel("median temperature (degC)")
    ax.set_title("Median temperature by file")
    ax.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    fig.savefig(figures_dir / "temp_by_file.png", dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4))
    for name, grp in merged.sort_values("timestamp").groupby("source_file"):
        ax.plot(grp["timestamp"], grp["temperature"], marker="o", linestyle="-", label=name)
    ax.set_ylabel("temperature (degC)")
    ax.set_title("Temperature over time")
    ax.legend(fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(figures_dir / "timeseries.png", dpi=120)
    plt.close(fig)

    print(f"Wrote 2 figure(s) to {figures_dir}")


if __name__ == "__main__":
    main()
