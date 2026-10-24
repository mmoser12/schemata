# Schemata

**Merge messy lab spreadsheets into one clean dataset, generate the script that did it, and automatically re-run everything downstream when new data arrives.**

Built for HackPSU 2026 · Challenge: *Build a tool that makes scientific research faster.*

---

## The problem

Two of the most common delays in research are the same chore done over and over.

**1. Cleaning and merging data by hand.** Every lab member names columns differently (`Temp (C)` vs `temperature_K`), mixes units (mg vs g, °C vs K), and uses different date formats. Someone spends hours merging those spreadsheets in Excel, and the cleaning can't be reproduced because nobody knows exactly what was done. Unit mistakes slip through silently.

**2. Re-running everything when new data shows up.** "I got new data, now I re-run five notebooks and remake every figure." Nobody remembers which steps depend on which files, so people either re-run everything (slow) or miss a step (wrong results and stale figures).

## What Schemata does

### Part 1: Spreadsheet merger that writes its own script

1. **Drop in your files.** CSV or Excel, from different people, instruments, or years.
2. **It matches columns across files.** `Temp (C)`, `temp_c`, and `temperature` map to the same target column.
3. **It detects and converts units.** Units are read from headers and value ranges, converted to a common standard, and validated with dimensional analysis. If one file is secretly in Fahrenheit, it gets flagged.
4. **It normalizes dates and types.** Mixed date formats and inconsistent types are standardized.
5. **You review and approve.** The UI shows proposed mappings and unit conflicts before anything is applied.
6. **You get a clean dataset and the cleaning script.** Output is a tidy table plus a generated Python/pandas script with built-in checks, so the cleaning is reproducible instead of a black box.

### Part 2: Auto-rerun analysis pipeline

1. **Register your steps.** Raw data → cleaning → analysis → figures, stored as a small dependency graph (DAG). The generated cleaning script from Part 1 becomes the first step automatically.
2. **Watch for changes.** When a raw file is added or changed, Schemata hashes the inputs and figures out which steps are affected.
3. **Re-run only what's needed.** Only the steps downstream of the change re-run. Everything else is reused from cache.
4. **See what's stale.** A dashboard shows the pipeline graph, which steps ran, which figures are up to date, and which are stale.

Think of it as a tiny Airflow built for one grad student.

> **Elevator pitch:** It takes messy lab spreadsheets with different column names and units, merges them into one clean dataset, and writes the Python script that did the cleaning. Then when new data comes in, it automatically re-runs only the analysis steps that changed and shows you which figures are out of date.

## Why not just Excel, OpenRefine, ChatGPT, or Airflow?

| Tool | Limitation | Schemata |
|---|---|---|
| Excel | Manual, not repeatable | Generates code that runs the same every time |
| OpenRefine / Trifacta | Interactive, not unit-aware | Unit detection and dimensional validation |
| ChatGPT | One-off answer, no guarantees | Deterministic, tested script; LLM only used for header matching |
| Airflow / Dagster | Built for data teams, heavy to set up | Lightweight DAG a single researcher can set up in minutes |
| Re-running notebooks by hand | Slow, easy to miss a step | Only affected steps re-run, stale figures are flagged |

## Architecture

```
  Upload / watch folder (Next.js)
        │
        ▼
  FastAPI backend ──► Supabase (files, project + pipeline metadata)
        │
        ├─ MERGER
        │   ├─ Profiler (Polars): types, null rates, value ranges
        │   ├─ Header matcher: rules first, small LLM call only for ambiguous names
        │   ├─ Unit engine (pint): detect, convert, dimension-check
        │   └─ Code generator: emits clean.py + validation report
        │
        └─ PIPELINE RUNNER
            ├─ DAG registry: steps and their inputs/outputs
            ├─ Change detector: file hashing to find what changed
            ├─ Executor: re-runs only downstream steps, caches the rest
            └─ Status tracker: fresh vs stale for every step and figure
        │
        ▼
  Dashboard (Next.js): mapping review, unit conflicts, DAG view, stale figures
```

**Design rule:** the LLM does one narrow job (guessing which messy header maps to which column). Everything else is deterministic so results are reproducible.

## Tech stack

| Layer | Tech | Why |
|---|---|---|
| Frontend | Next.js, TypeScript | Upload flow, mapping review, pipeline dashboard |
| Backend | FastAPI (Python) | Merger and pipeline runner APIs |
| Data processing | pandas / Polars, DuckDB | Profiling, merging, cross-file checks |
| Units | pint | Unit parsing, conversion, dimensional analysis |
| Pipeline | Custom DAG + file hashing | Incremental re-runs and staleness tracking |
| Storage | Supabase | Files, project and pipeline metadata |
| Output | CSV / Parquet, generated Python, figures | Clean data, reproducible script, up-to-date plots |

## MVP scope (hackathon)

**Merger**
- [ ] File upload (CSV, Excel)
- [ ] Column profiling and type inference
- [ ] Header-to-target mapping with review UI
- [ ] Unit detection, conversion, and conflict flags
- [ ] Date format normalization
- [ ] Merged clean dataset export
- [ ] Generated `clean.py` (pandas) with validation checks

**Pipeline**
- [ ] Register steps as a DAG (clean → analyze → figures)
- [ ] Detect changed raw files via hashing
- [ ] Re-run only affected downstream steps
- [ ] Dashboard showing step status and stale figures

**Stretch**
- [ ] Schema drift alerts when a new file has unexpected columns
- [ ] Run history and timing comparison (full re-run vs incremental)

## Demo plan

1. Drop in a pile of messy sensor/lab files. In seconds, one clean table appears, and Schemata flags the file where temperature was recorded in Fahrenheit while everything else was Celsius.
2. Show the generated `clean.py` script.
3. Show the pipeline graph: clean → analysis → three figures, all green.
4. Drop in a new month of data. The affected steps light up, re-run automatically, and the figures update, while unaffected steps are skipped. Show the time saved versus re-running everything.

## Getting started

> Setup instructions will be filled in as the project is built.

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload

# Frontend
cd frontend
npm install
npm run dev
```

Environment variables (`.env`):

```
SUPABASE_URL=
SUPABASE_ANON_KEY=
LLM_API_KEY=
```

## Repo structure 

```
schemata/
├── backend/
│   ├── merger/      # profiling, header matching, units, code generation
│   └── pipeline/    # DAG registry, change detection, executor
├── frontend/        # Next.js app and dashboard
├── sample_data/     # messy demo files
└── README.md
```

## Team

- Matthew Moser
- Denis Hoxhaj

