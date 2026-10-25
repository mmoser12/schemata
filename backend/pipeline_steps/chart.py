"""chart.py: an SVG chart from merged.csv (a built-in pipeline step).

Usage: python chart.py PARAMS_JSON MERGED_CSV OUT_SVG OUT_DATA_CSV

PARAMS_JSON:
    chart     "bar" | "line" | "scatter"
    y         column to plot (numeric)
    x         x column for line/scatter (numeric or datetime); for bar, the category column
    group_by  series column for line/scatter (default "source_file"; "" for one series)
    agg       bar aggregation: "median" | "mean" | "sum" | "count" | "min" | "max"
    title     chart title (optional)
    x_label / y_label  axis labels (optional; default column name + unit)
    x_is_time  true when x is a datetime column

OUT_DATA_CSV holds exactly the numbers that were plotted, so every figure ships
with its data (for checking, re-plotting, or a journal's data policy).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import svgchart  # noqa: E402

AGGS = {"median", "mean", "sum", "count", "min", "max"}


def main() -> None:
    params = json.loads(Path(sys.argv[1]).read_text())
    df = pd.read_csv(sys.argv[2])
    svg_out, data_out = Path(sys.argv[3]), Path(sys.argv[4])
    kind = params.get("chart", "bar")
    y = params["y"]
    if y not in df.columns:
        raise SystemExit(f"column '{y}' is not in merged.csv (columns: {', '.join(df.columns)})")
    y_label = params.get("y_label") or y
    df[y] = pd.to_numeric(df[y], errors="coerce")

    if kind == "bar":
        x = params.get("x") or "source_file"
        agg = params.get("agg", "median")
        if agg not in AGGS:
            raise SystemExit(f"unknown aggregation '{agg}'")
        grouped = df.groupby(x, sort=False)[y].agg(agg)
        data = grouped.reset_index().rename(columns={y: f"{agg}_{y}"})
        title = params.get("title") or f"{agg.capitalize()} {y} by {x}"
        svg = svgchart.bar_chart([str(k) for k in grouped.index], grouped.tolist(), title=title,
                                 subtitle=f"{agg} of {len(df[y].dropna())} values", y_label=y_label)
    elif kind in ("line", "scatter"):
        x = params["x"]
        if x not in df.columns:
            raise SystemExit(f"column '{x}' is not in merged.csv")
        x_is_time = bool(params.get("x_is_time"))
        if x_is_time:
            df[x] = pd.to_datetime(df[x], errors="coerce")
            xs = svgchart.to_unix(df[x])
        else:
            df[x] = pd.to_numeric(df[x], errors="coerce")
            xs = df[x].tolist()
        group_by = params.get("group_by", "source_file")
        keys = df[group_by].astype(str).tolist() if group_by and group_by in df.columns else [y] * len(df)
        series: dict[str, list[tuple[float, float]]] = {}
        for k, xv, yv in zip(keys, xs, df[y].tolist()):
            if xv is None or pd.isna(xv) or pd.isna(yv):
                continue
            series.setdefault(k, []).append((float(xv), float(yv)))
        cols = [c for c in [group_by, x, y] if c and c in df.columns]
        data = df[cols].dropna(subset=[x, y])
        title = params.get("title") or f"{y} vs {x}"
        svg = svgchart.xy_chart(series, title=title, subtitle=f"{len(data)} points",
                                x_label=params.get("x_label") or x, y_label=y_label,
                                x_is_time=x_is_time, lines=(kind == "line"))
    else:
        raise SystemExit(f"unknown chart type '{kind}'")

    svg_out.parent.mkdir(parents=True, exist_ok=True)
    svg_out.write_text(svg, encoding="utf-8")
    data.to_csv(data_out, index=False)
    print(f"Wrote {svg_out} and its data ({len(data)} rows) to {data_out}")


if __name__ == "__main__":
    main()
