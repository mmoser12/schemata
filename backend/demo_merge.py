"""Run the merger on a folder of files from the command line.

    python demo_merge.py                  # uses ../sample_data
    python demo_merge.py path/to/folder   # any folder of CSV/Excel files

Writes output/merged.csv and output/clean.py next to this file.
"""

import sys
from pathlib import Path

from merger import apply_plan, build_plan, generate_clean_script, read_table

HERE = Path(__file__).parent
data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "sample_data"
out_dir = HERE / "output"
out_dir.mkdir(exist_ok=True)

files = sorted(p for p in data_dir.iterdir() if p.suffix.lower() in {".csv", ".xlsx", ".xls", ".xlsm"})
tables = {p.name: read_table(p) for p in files}

plan = build_plan(tables)
print("\nMAPPINGS")
for fp in plan.files:
    print(f"  {fp.file}")
    for m in fp.mappings:
        extra = f"  [{m.unit}, {m.unit_source}]" if m.unit else (f"  [{m.date_format}]" if m.date_format else "")
        print(f"    {m.source!r:22} -> {m.target:12} {m.method}:{m.confidence}{extra}")

merged, validation = apply_plan(tables, plan)
print("\nISSUES")
for i in [*plan.issues, *validation]:
    if i.severity != "info":
        where = " / ".join(x for x in [i.file, i.column] if x)
        print(f"  {i.severity.upper():8} {where}: {i.message}")

merged.to_csv(out_dir / "merged.csv", index=False)
(out_dir / "clean.py").write_text(generate_clean_script(plan))
print(f"\nWrote {len(merged)} rows to {out_dir / 'merged.csv'} and the script to {out_dir / 'clean.py'}")
print(merged.groupby("source_file")[["temperature", "mass", "pressure"]].median().round(2))
