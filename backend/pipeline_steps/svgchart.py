"""A small, dependency-free SVG chart renderer (bar, line, scatter).

Figures are standalone SVG files: vector (crisp in papers and slides), tiny,
and they follow the viewer's light/dark setting through an embedded
prefers-color-scheme style. Every mark carries a <title>, so opening the
file in a browser gives hover tooltips.

Design rules (from the dataviz guidance this project follows): categorical
colors in a fixed order, 2px lines, >= 8px markers with a surface ring,
4px rounded bar ends, hairline recessive grid, text never in series colors,
a legend whenever there are 2+ series.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence
from xml.sax.saxutils import escape

# Validated categorical palette (light, dark). Order matters: it is what keeps
# neighbouring series distinguishable for colour-blind readers.
SERIES = [
    ("#2a78d6", "#3987e5"), ("#eb6834", "#d95926"), ("#1baf7a", "#199e70"), ("#eda100", "#c98500"),
    ("#e87ba4", "#d55181"), ("#008300", "#008300"), ("#6250d6", "#9085e9"), ("#e34948", "#e66767"),
]
OTHER = ("#8a8984", "#77766f")
MAX_LINE_SERIES = len(SERIES)
MAX_SCATTER_SERIES = 3  # beyond three, scatter colours stop being reliably distinct

W, H = 720, 420
TOP, RIGHT, BOTTOM, LEFT = 64, 28, 56, 72


def _style(n_series: int) -> str:
    light = "".join(f"--s{i}:{c[0]};" for i, c in enumerate(SERIES[:n_series]))
    dark = "".join(f"--s{i}:{c[1]};" for i, c in enumerate(SERIES[:n_series]))
    return (
        "<style>"
        f":root{{--bg:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--grid:#e4e3df;--axis:#bdbcb6;--other:{OTHER[0]};{light}}}"
        f"@media (prefers-color-scheme:dark){{:root{{--bg:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;"
        f"--grid:#2f2f2d;--axis:#55554f;--other:{OTHER[1]};{dark}}}}}"
        "text{font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;fill:var(--ink2);font-size:14px}"
        ".title{fill:var(--ink);font-size:19px;font-weight:600}.sub{font-size:14px}"
        ".tick{font-variant-numeric:tabular-nums}.grid{stroke:var(--grid);stroke-width:1}"
        ".axis{stroke:var(--axis);stroke-width:1}.label{fill:var(--ink);font-size:14px}"
        "</style>"
    )


def nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    """Round tick values covering [lo, hi] (1/2/5 x 10^k steps)."""
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return [0.0, 1.0]
    if hi == lo:
        pad = abs(lo) * 0.1 or 1.0
        lo, hi = lo - pad, hi + pad
    raw = (hi - lo) / max(n, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    ticks, v = [], start
    while v <= hi + step * 0.5 and len(ticks) < 20:
        ticks.append(round(v, 10))
        v += step
    if ticks[-1] < hi:
        ticks.append(round(ticks[-1] + step, 10))
    return ticks


def fmt_num(v: float) -> str:
    a = abs(v)
    if a >= 1e6:
        return f"{v / 1e6:g}M"
    if a >= 1e4:
        return f"{v / 1e3:g}K"
    if a >= 100 or v == int(v):
        return f"{v:,.0f}" if a >= 1000 else f"{v:g}"
    return f"{v:.3g}"


_TIME_STEPS = [
    (timedelta(minutes=15), "%H:%M"), (timedelta(hours=1), "%H:%M"), (timedelta(hours=3), "%H:%M"),
    (timedelta(hours=6), "%b %d %H:%M"), (timedelta(hours=12), "%b %d %H:%M"), (timedelta(days=1), "%b %d"),
    (timedelta(days=2), "%b %d"), (timedelta(days=7), "%b %d"), (timedelta(days=14), "%b %d"),
    (timedelta(days=30), "%b %Y"), (timedelta(days=91), "%b %Y"), (timedelta(days=365), "%Y"),
]


def time_ticks(lo: float, hi: float, n: int = 7) -> tuple[list[float], str]:
    """Ticks for unix-second values, aligned to round clock/calendar boundaries."""
    span = max(hi - lo, 1.0)
    step, fmt = next(((s, f) for s, f in _TIME_STEPS if span / s.total_seconds() <= n), _TIME_STEPS[-1])
    sec = step.total_seconds()
    start = math.ceil(lo / sec) * sec
    ticks = []
    t = start
    while t <= hi and len(ticks) < 20:
        ticks.append(t)
        t += sec
    return (ticks or [lo, hi]), fmt


def _fmt_time(t: float, fmt: str) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime(fmt)


class _Canvas:
    def __init__(self, title: str, subtitle: str, n_series: int, desc: str):
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
            f'role="img" aria-labelledby="t d">',
            f'<title id="t">{escape(title)}</title><desc id="d">{escape(desc)}</desc>',
            _style(n_series),
            f'<rect width="{W}" height="{H}" fill="var(--bg)"/>',
            f'<text class="title" x="{LEFT}" y="28">{escape(title)}</text>',
        ]
        if subtitle:
            self.parts.append(f'<text class="sub" x="{LEFT}" y="49">{escape(subtitle)}</text>')

    def add(self, s: str) -> None:
        self.parts.append(s)

    def svg(self) -> str:
        return "\n".join(self.parts + ["</svg>", ""])


def _legend_rows(names: Sequence[str]) -> list[list[tuple[str, int]]]:
    """Lay legend items out left to right, wrapping at the plot width."""
    rows: list[list[tuple[str, int]]] = [[]]
    x = LEFT
    for name in names:
        label = name if len(name) <= 32 else name[:31] + "…"
        width = 30 + int(7.5 * len(label))
        if rows[-1] and x + width > W - RIGHT:
            rows.append([])
            x = LEFT
        rows[-1].append((label, width))
        x += width
    return rows


def _legend(c: _Canvas, names: Sequence[str], colors: Sequence[str], marker: str = "line") -> int:
    """Draw the legend under the subtitle. Returns the plot's top edge."""
    if len(names) < 2:
        return TOP
    i = 0
    rows = _legend_rows(names)
    for r, row in enumerate(rows):
        x, y = LEFT, 76 + 20 * r
        for label, width in row:
            color = colors[i]
            i += 1
            if marker == "line":
                c.add(f'<line x1="{x}" y1="{y - 5}" x2="{x + 18}" y2="{y - 5}" stroke="{color}" '
                      f'stroke-width="2" stroke-linecap="round"/>')
            else:
                c.add(f'<circle cx="{x + 9}" cy="{y - 5}" r="4" fill="{color}"/>')
            c.add(f'<text x="{x + 24}" y="{y}">{escape(label)}</text>')
            x += width
    return 76 + 20 * (len(rows) - 1) + 18


def _y_axis(c: _Canvas, ticks: list[float], y_of, label: str, top: int = TOP) -> None:
    for t in ticks:
        y = y_of(t)
        c.add(f'<line class="grid" x1="{LEFT}" y1="{y:.1f}" x2="{W - RIGHT}" y2="{y:.1f}"/>')
        c.add(f'<text class="tick" x="{LEFT - 8}" y="{y + 4:.1f}" text-anchor="end">{escape(fmt_num(t))}</text>')
    mid = top + (H - top - BOTTOM) / 2
    c.add(f'<text x="18" y="{mid:.0f}" text-anchor="middle" transform="rotate(-90 18 {mid:.0f})">{escape(label)}</text>')


def _empty(title: str, message: str) -> str:
    c = _Canvas(title, "", 0, message)
    c.add(f'<text x="{W / 2}" y="{H / 2}" text-anchor="middle">{escape(message)}</text>')
    return c.svg()


def bar_chart(categories: Sequence[str], values: Sequence[float], *, title: str, subtitle: str = "",
              y_label: str = "", value_fmt=fmt_num) -> str:
    pairs = [(str(k), float(v)) for k, v in zip(categories, values) if v is not None and math.isfinite(v)]
    if not pairs:
        return _empty(title, "No values to plot")
    lo = min(0.0, min(v for _, v in pairs))
    hi = max(0.0, max(v for _, v in pairs))
    ticks = nice_ticks(lo, hi)
    y0, y1 = ticks[0], ticks[-1]
    ph = H - TOP - BOTTOM
    y_of = lambda v: TOP + ph * (1 - (v - y0) / (y1 - y0))  # noqa: E731
    c = _Canvas(title, subtitle, 1, f"Bar chart of {y_label or 'values'} for {len(pairs)} categories")
    _y_axis(c, ticks, y_of, y_label)

    band = (W - LEFT - RIGHT) / len(pairs)
    bw = min(24.0 if len(pairs) > 12 else 48.0, band * 0.6)
    base = y_of(0.0)
    c.add(f'<line class="axis" x1="{LEFT}" y1="{base:.1f}" x2="{W - RIGHT}" y2="{base:.1f}"/>')
    for i, (cat, v) in enumerate(pairs):
        cx = LEFT + band * (i + 0.5)
        top = y_of(v)
        h = abs(base - top)
        r = min(4.0, h / 2, bw / 2)
        x = cx - bw / 2
        if v >= 0:  # rounded data end, square at the baseline
            d = (f"M{x:.1f},{base:.1f} V{top + r:.1f} Q{x:.1f},{top:.1f} {x + r:.1f},{top:.1f} "
                 f"H{x + bw - r:.1f} Q{x + bw:.1f},{top:.1f} {x + bw:.1f},{top + r:.1f} V{base:.1f} Z")
            ly = top - 6
        else:
            d = (f"M{x:.1f},{base:.1f} V{top - r:.1f} Q{x:.1f},{top:.1f} {x + r:.1f},{top:.1f} "
                 f"H{x + bw - r:.1f} Q{x + bw:.1f},{top:.1f} {x + bw:.1f},{top - r:.1f} V{base:.1f} Z")
            ly = top + 16
        c.add(f'<path d="{d}" fill="var(--s0)"><title>{escape(cat)}: {escape(value_fmt(v))}</title></path>')
        c.add(f'<text class="label tick" x="{cx:.1f}" y="{ly:.1f}" text-anchor="middle">{escape(value_fmt(v))}</text>')
        label = cat if len(cat) <= max(6, int(band / 7)) else cat[:max(5, int(band / 7)) - 1] + "…"
        c.add(f'<text x="{cx:.1f}" y="{H - BOTTOM + 20}" text-anchor="middle">{escape(label)}'
              f'<title>{escape(cat)}</title></text>')
    return c.svg()


def xy_chart(series: dict[str, list[tuple[float, float]]], *, title: str, subtitle: str = "", x_label: str = "",
             y_label: str = "", x_is_time: bool = False, lines: bool = True) -> str:
    """Line (lines=True) or scatter chart. x values are unix seconds when x_is_time."""
    series = {k: sorted((x, y) for x, y in pts if math.isfinite(x) and math.isfinite(y))
              for k, pts in series.items()}
    series = {k: v for k, v in series.items() if v}
    if not series:
        return _empty(title, "No values to plot")

    cap = MAX_LINE_SERIES if lines else MAX_SCATTER_SERIES
    names = list(series)
    if len(names) > cap:  # fold the tail into "Other" rather than inventing colours
        keep, rest = names[:cap - 1], names[cap - 1:]
        other = [p for k in rest for p in series[k]]
        series = {**{k: series[k] for k in keep}, f"Other ({len(rest)})": sorted(other)}
        names = list(series)
    colors = [f"var(--s{i})" for i in range(len(names))]
    if names[-1].startswith("Other ("):
        colors[-1] = "var(--other)"

    xs = [x for pts in series.values() for x, _ in pts]
    ys = [y for pts in series.values() for _, y in pts]
    yt = nice_ticks(min(ys), max(ys))
    y0, y1 = yt[0], yt[-1]
    x_lo, x_hi = min(xs), max(xs)
    if x_is_time:
        xt, tfmt = time_ticks(x_lo, x_hi)
        if x_hi == x_lo:
            x_lo, x_hi = x_lo - 3600, x_hi + 3600
    else:
        xt = nice_ticks(x_lo, x_hi, 6)
        x_lo, x_hi = xt[0], xt[-1]
    kind = "Line" if lines else "Scatter"
    c = _Canvas(title, subtitle, len(names), f"{kind} chart of {y_label} against {x_label}, {len(names)} series")
    top = _legend(c, names, colors, "line" if lines else "dot")
    pw, ph = W - LEFT - RIGHT, H - top - BOTTOM
    x_of = lambda v: LEFT + pw * (v - x_lo) / ((x_hi - x_lo) or 1)  # noqa: E731
    y_of = lambda v: top + ph * (1 - (v - y0) / ((y1 - y0) or 1))  # noqa: E731
    _y_axis(c, yt, y_of, y_label, top)
    c.add(f'<line class="axis" x1="{LEFT}" y1="{H - BOTTOM}" x2="{W - RIGHT}" y2="{H - BOTTOM}"/>')
    for t in xt:
        x = x_of(t)
        if LEFT - 1 <= x <= W - RIGHT + 1:
            text = _fmt_time(t, tfmt) if x_is_time else fmt_num(t)
            c.add(f'<text class="tick" x="{x:.1f}" y="{H - BOTTOM + 20}" text-anchor="middle">{escape(text)}</text>')
    c.add(f'<text x="{LEFT + pw / 2:.0f}" y="{H - 12}" text-anchor="middle">{escape(x_label)}</text>')

    for name, color in zip(names, colors):
        pts = series[name]
        if lines and len(pts) > 1:
            d = " ".join(f"{'M' if i == 0 else 'L'}{x_of(x):.1f},{y_of(y):.1f}" for i, (x, y) in enumerate(pts))
            c.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2" '
                  f'stroke-linejoin="round" stroke-linecap="round"/>')
        for x, y in pts:
            xl = _fmt_time(x, "%Y-%m-%d %H:%M") if x_is_time else fmt_num(x)
            c.add(f'<circle cx="{x_of(x):.1f}" cy="{y_of(y):.1f}" r="4" fill="{color}" stroke="var(--bg)" '
                  f'stroke-width="2"><title>{escape(name)}: {escape(xl)}, {escape(fmt_num(y))}</title></circle>')
    return c.svg()


def to_unix(values) -> list[Optional[float]]:
    out = []
    for v in values:
        try:
            out.append(v.timestamp())
        except (AttributeError, ValueError, OverflowError):
            out.append(None)
    return out
