"""Reading raw files the way real lab exports actually look.

Instrument and spreadsheet exports are rarely clean CSVs. sniff_options()
looks at a file once and records *how* to read it:

  encoding   utf-8-sig, cp1252 or latin-1 (first that decodes)
  sep        ",", ";", tab or "|"
  decimal    "," when a semicolon/tab file uses decimal commas (21,5)
  skiprows   metadata lines above the real header ("Instrument: X", blank lines)
  sheet      for Excel: the first sheet that has data

Those options are stored in the merge plan and written into clean.py, so the
generated script reads every file exactly the way the app did. read_file() and
tidy() are copied verbatim into clean.py (see codegen) for the same reason, so
they may only use `pd` -- no other module globals.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from pathlib import Path
from typing import Optional, Union

import pandas as pd

EXCEL_SUFFIXES = {".xlsx", ".xlsm"}
TEXT_SUFFIXES = {".csv", ".tsv", ".txt"}
SUPPORTED_SUFFIXES = EXCEL_SUFFIXES | TEXT_SUFFIXES

_ENCODINGS = ["utf-8-sig", "cp1252", "latin-1"]
_DELIMITERS = [",", ";", "\t", "|"]
_NUMBER = re.compile(r"^[-+]?(\d+([.,]\d*)?|[.,]\d+)([eE][-+]?\d+)?$")
_DECIMAL_COMMA = re.compile(r"^[-+]?\d+,\d+$")


class UnsupportedFile(ValueError):
    """The file type can't be read (e.g. legacy .xls)."""


def tidy(df):
    """Normalize a freshly read table: trimmed unique headers, no empty rows/columns."""
    names, seen = [], {}
    for c in df.columns:
        name = str(c).strip()
        if name in seen:
            seen[name] += 1
            name = f"{name}.{seen[name]}"
        else:
            seen[name] = 0
        names.append(name)
    df.columns = names
    empty_cols = [c for c in df.columns if c.startswith("Unnamed:") and df[c].isna().all()]
    df = df.drop(columns=empty_cols).dropna(how="all")
    return df.reset_index(drop=True)


def read_file(path, options):
    """Read one raw file with explicit, recorded options (see sniff_options)."""
    options = options or {}
    if str(path).lower().endswith((".xlsx", ".xlsm")):
        df = pd.read_excel(path, sheet_name=options.get("sheet", 0), skiprows=options.get("skiprows", 0))
    else:
        df = pd.read_csv(path, sep=options.get("sep", ","), encoding=options.get("encoding", "utf-8-sig"),
                         decimal=options.get("decimal", "."), skiprows=options.get("skiprows", 0))
    return tidy(df)


def _is_number(cell: str) -> bool:
    return bool(_NUMBER.match(cell.strip()))


def _header_row(rows: list[list[str]]) -> int:
    """Index of the first row that looks like a header for the rows below it.

    A header has (nearly) as many filled cells as the data rows and is mostly
    text. Metadata lines above it ("Operator,Bob") are narrower.
    """
    widths = [sum(1 for c in r if str(c).strip()) for r in rows]
    for i in range(min(len(rows), 30)):
        below = [w for w in widths[i + 1:i + 11] if w > 0]
        if widths[i] < 2 or not below:
            continue
        typical = Counter(below).most_common(1)[0][0]
        filled = [str(c) for c in rows[i] if str(c).strip()]
        numeric_share = sum(_is_number(c) for c in filled) / len(filled)
        if widths[i] >= max(2, 0.6 * typical) and numeric_share <= 0.34:
            return i
    return 0


def _decode(data: bytes) -> tuple[str, str]:
    for enc in _ENCODINGS:
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace"), "latin-1"  # unreachable: latin-1 decodes anything


def _delimiter(lines: list[str], suffix: str) -> str:
    if suffix == ".tsv":
        return "\t"
    best, best_score = ",", -1.0
    sample = [ln for ln in lines[:60] if ln.strip()]
    for d in _DELIMITERS:
        counts = [len(next(csv.reader([ln], delimiter=d))) - 1 for ln in sample]
        counts = [c for c in counts if c > 0]
        if not counts:
            continue
        typical, freq = Counter(counts).most_common(1)[0]
        score = freq * typical  # consistent and wide wins
        if score > best_score:
            best, best_score = d, score
    return best


def sniff_options(path: Union[str, Path]) -> dict:
    """Work out how to read `path`. Raises UnsupportedFile for unreadable types."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".xls":
        raise UnsupportedFile("legacy .xls files aren't supported; save the sheet as .xlsx or .csv")
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnsupportedFile(f"unsupported file type '{suffix}'; use .csv, .tsv, .txt, .xlsx or .xlsm")

    if suffix in EXCEL_SUFFIXES:
        book = pd.read_excel(path, sheet_name=None, header=None, nrows=60)
        # First sheet with a header *and* at least one data row (skips notes/cover sheets).
        sheet, raw = next(((name, df) for name, df in book.items() if len(df.dropna(how="all")) >= 2),
                          next(iter(book.items())))
        rows = [["" if pd.isna(v) else str(v) for v in row] for row in raw.itertuples(index=False)]
        options: dict = {"skiprows": _header_row(rows)}
        if len(book) > 1:
            options["sheet"] = sheet
        return options

    text, encoding = _decode(path.read_bytes())
    lines = text.splitlines()
    sep = _delimiter(lines, suffix)
    rows = [next(csv.reader([ln], delimiter=sep)) if ln.strip() else [] for ln in lines[:60]]
    skip = _header_row(rows)
    options = {"sep": sep, "encoding": encoding, "skiprows": skip}

    if sep != ",":
        cells = [c.strip() for r in rows[skip + 1:] for c in r if c.strip()]
        if cells and sum(bool(_DECIMAL_COMMA.match(c)) for c in cells) / len(cells) >= 0.2:
            options["decimal"] = ","
    return options


def load(path: Union[str, Path], options: Optional[dict] = None) -> tuple[pd.DataFrame, dict]:
    """Read a file, sniffing options unless they're given. Returns (table, options)."""
    if options is None:
        options = sniff_options(path)
    return read_file(Path(path), options), options


def read_table(source: Union[str, Path, io.BytesIO], filename: Optional[str] = None) -> pd.DataFrame:
    """Convenience: read a path (or an uploaded file-like object) with sniffed options."""
    if isinstance(source, (str, Path)):
        return load(source)[0]
    import tempfile

    suffix = Path(filename or "upload.csv").suffix
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / f"upload{suffix}"
        p.write_bytes(source.read())
        return load(p)[0]


def describe_options(options: dict) -> str:
    """Human summary for the UI: 'semicolon-separated, decimal comma, 3 header lines skipped'."""
    parts = []
    names = {",": "comma-separated", ";": "semicolon-separated", "\t": "tab-separated", "|": "pipe-separated"}
    if "sep" in options:
        parts.append(names.get(options["sep"], f"'{options['sep']}'-separated"))
    if options.get("decimal") == ",":
        parts.append("decimal comma")
    if options.get("encoding") and options["encoding"] not in ("utf-8", "utf-8-sig"):
        parts.append(f"{options['encoding']} encoding")
    if options.get("sheet") is not None:
        parts.append(f"sheet '{options['sheet']}'")
    if options.get("skiprows"):
        n = options["skiprows"]
        parts.append(f"{n} line{'s' if n != 1 else ''} above the header skipped")
    return ", ".join(parts) or "standard format"
