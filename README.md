# Schemata

**Merge messy lab spreadsheets into one clean dataset, get the script that did it, and re-run only what changed when new data arrives.**

Built for HackPSU 2026 · Challenge: *Build a tool that makes scientific research faster.*

Schemata is a local web app. It runs on your computer, keeps your data in one folder, needs only Python, and has no build step, no Node.js and no external services.

---

## The problem

Two of the most common delays in research are the same chore done over and over.

**1. Cleaning and merging data by hand.** Every lab member names columns differently (`Temp (C)` vs `temperature_K`), mixes units (mg vs g, °C vs K), and uses different date formats. Someone spends hours merging those spreadsheets in Excel, and the cleaning can't be reproduced because nobody knows exactly what was done. Unit mistakes slip through silently.

**2. Re-running everything when new data shows up.** "I got new data, now I re-run five notebooks and remake every figure." Nobody remembers which steps depend on which files, so people either re-run everything (slow) or miss a step (wrong results and stale figures).

## Quick start

You need **Python 3.12 or newer**.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python run.py                        # opens http://localhost:8000
```

On the home page, click **Try the sample project**. It loads three messy files from three "lab members", one of which secretly recorded temperature in Fahrenheit.

`run.py` options: `--port 9000`, `--data ~/lab-data` (where projects are stored, default `./data`), `--no-browser`. If the port is busy it picks the next free one.

## What Schemata does

A project moves through six tabs. Each tab shows whether it's done or needs attention, and suggests the next step.

| Tab | What happens |
|---|---|
| **1 · Files** | Drag in CSV, TSV, TXT or Excel files, or save them straight into the project's **watch folder**. Each file's format is detected: comma, semicolon or tab separators, decimal commas (`21,5`), UTF-8/Windows encodings, title or metadata lines above the real header, and the right sheet in a multi-sheet workbook. Originals are never modified. |
| **2 · Schema** | Define the clean columns you want: an ID, a datetime, *quantities* with a unit (converted), unitless *numbers* like pH (range-checked), and text. Or click **Suggest from my files**: Schemata groups columns that mean the same thing across files and proposes names, kinds and units. |
| **3 · Review** | Every column of every file shows what it maps to, its unit or date format, **how real values change** (`74.2 → 23.44 °C`), why it was matched that way, and any issues inline. Change a mapping, unit or date format with a dropdown. Edits save instantly with **undo/redo** and nothing is applied until you **approve**. |
| **4 · Results** | The merged table, searchable and sortable, with per-column ranges and units. Download it as CSV or Excel, along with the generated `clean.py` and a reproducibility bundle. |
| **5 · Pipeline** | A live graph of `clean → your analysis steps`, showing which steps are stale and why. Add summary tables, bar/line/scatter charts, or your own Python scripts. Only stale steps re-run, and the run history shows the time saved by caching. |
| **6 · Activity** | A permanent audit trail: every upload (with file hashes), every mapping change with its previous value, every approval and run. Exportable as CSV. |

### How the merge works

1. **Header matching.** Exact synonyms first, then fuzzy string matching (`temprature` → `temperature`). Units are read from headers like `Temp (C)`, `mass_mg` and `Conc (mM)`. Case matters, so `mM` is millimolar and `mm` is millimetres.
2. **Unit inference across files.** A bare `Temp` column is tested against every candidate unit, and the reading whose values agree with the other files wins. That is how a file secretly in °F gets caught and converted.
3. **Dates.** Each file gets one explicit format. When a date is ambiguous (`09/01` could be Sep 1 or Jan 9), Schemata follows a file whose format is unambiguous, or else picks the reading with the tighter date range, and tells you.
4. **Validation.** Missing or duplicate IDs, unparseable dates, and values outside a column's valid range are flagged before anything runs.
5. **`clean.py`.** Approval writes a standalone script that needs only pandas (plus openpyxl for Excel). Every conversion is written out as a plain formula with a comment saying where it came from, and every file is read with the exact options Schemata detected. Anyone can re-run it with `python clean.py raw merged.csv`.

User edits are stored as *overrides* on top of the automatic plan. Adding a file, changing a file or editing the schema never throws away a decision you already made.

### Watch folder and auto-run

Each project has a `raw/` folder, shown on the Files tab with a copy button. Schemata notices files saved there within a few seconds, whether from an instrument PC, a synced folder or a script. With **Auto-run** on:

- a new or changed file whose columns all map cleanly (no warnings) is approved and processed automatically, and only the stale steps re-run;
- a file that needs a human (unit conflict, a column the schema doesn't know about, missing columns) waits for review, and the Activity log says why.

New files are checked for **schema drift**: columns no approved file has, or columns every approved file has but this one is missing.

### Pipeline steps

Every step declares the files it reads and writes. The dependency graph is derived from those declarations, and files are compared by SHA-256 hash. If a step re-runs but writes byte-identical output, the steps after it are skipped too.

| Step | Output | Settings |
|---|---|---|
| `clean` (always first) | `output/merged.csv` | the approved `clean.py` |
| Summary statistics | `output/<name>.csv` | count, mean, median, std, min and max per source file |
| Chart | `output/figures/<name>.svg` + `.csv` | bar (median/mean/… per group), line, or scatter; the CSV holds exactly the plotted numbers |
| Your script | files you declare under `output/` | `python scripts/<script>.py <inputs…> <outputs…>` |

Charts are drawn by Schemata itself as SVG. They're vector, so they stay sharp in papers and slides. They follow light/dark mode and use a colour-blind-safe palette in a fixed order. Every point has a hover tooltip when the file is opened in a browser, and every figure ships with its data table.

Custom scripts run with the same Python as Schemata, so they can use pandas. Upload them in the *Add step* dialog or put them in the project's `scripts/` folder.

### Reproducibility bundle

**Export bundle** (top of every project) downloads a zip containing:
- the raw files;
- `clean.py`, the schema, the mapping decisions and the pipeline settings;
- every output;
- the full activity log;
- `MANIFEST.json` with the SHA-256 of every file plus the Python and package versions.

It's everything someone else needs to check or reproduce the result.

## Design: what we took from tools researchers already use

We looked at what makes popular science software work and built those ideas in without adding dependencies.

| Inspiration | What makes it good | How Schemata applies it |
|---|---|---|
| **PhET simulations** ([implicit scaffolding](https://arxiv.org/pdf/1306.6544)) | Usable with no instructions: the interface guides through affordances, constraints and instant feedback; complex ideas are split into tabs | A numbered tab workflow that marks what's done or needs attention; a "Next" button on every tab; one-click sample project; live before → after values |
| **LabArchives / ELNs** ([audit trails, provenance](https://pubs.rsc.org/en/content/articlepdf/2015/sc/c4sc02128b)) | Every change records who, when and the *previous value*; provenance of each object is preserved | An activity log with previous → new values, file hashes on every version, and a bundle with a hash manifest |
| **Wrangler / OpenRefine** ([Wrangler paper](https://idl.uw.edu/papers/wrangler), [OpenRefine history](https://guides.library.illinois.edu/openrefine/undoredo)) | Preview an operation's effect before committing; undo/redo history; an exportable, re-runnable recipe | Value previews per column, undo/redo, edits stored as a recipe (overrides), and `clean.py` as the re-runnable recipe |
| **ChemDraw / publication tools** | Output that goes straight into a paper | SVG figures with their data, Excel and CSV exports |
| **MolView / web tools** | Instant start, nothing to install | One command to start, works in any browser, the sample project in one click |
| **Nielsen's usability heuristics** | Visibility of status, error prevention, user control, recovery | Live run status and stale markers, validation before running, confirm dialogs for destructive actions, clear error messages with a way forward |

Other UX details: keyboard shortcuts (`?` shows them all), light/dark/system theme, a layout that works on phones, status shown with icon + text (never colour alone), and focus states for keyboard users.

## What gets installed, and what gets written where

**Installing.** `pip install -r requirements.txt` puts packages **only inside the virtual environment** (`.venv/`). pip also keeps a download cache in your user cache folder (`~/.cache/pip` on Linux, `~/Library/Caches/pip` on macOS, `%LocalAppData%\pip\Cache` on Windows); add `--no-cache-dir` to skip it. Nothing else is installed: no Node.js, no npm packages, no system services, no fonts, no browser extensions.

**Running.** We measured this by running a full session with an empty fake home directory and listing every file created afterwards. The only files written were in the data folder:

```
data/                         (or wherever --data points)
  schemata.db                 project list, file hashes, activity log, run history (SQLite)
  projects/<id>/raw/          your files, exactly as added (also the watch folder)
  projects/<id>/clean.py      the generated script
  projects/<id>/*.json        schema, mapping decisions, pipeline settings
  projects/<id>/output/       merged.csv, summaries, figures, pipeline state
```

- **No files** go to your home directory, the system temp folder, or the source tree. `run.py` disables `__pycache__` writing, and pipeline steps run with `PYTHONDONTWRITEBYTECODE=1`. Running the tests does create `__pycache__/` folders, which are gitignored.
- **No network.** The app makes no outbound connections: no telemetry, no CDNs, no web fonts (system fonts only), no update checks. The UI is served from the same local process.
- `run.py` opens your default browser on the local URL (skip with `--no-browser`). Your browser keeps its normal history and cache for that page, the same as for any local page.
- A custom script you add to a pipeline runs as you and can write wherever it likes. Schemata only declares and caches the outputs you list. If such a script uses matplotlib, its cache is redirected into `data/.cache/`.

To remove Schemata completely, delete the repo folder (including `.venv/`) and the data folder.

## Dependencies

Everything Schemata needs beyond Python is in `requirements.txt`, pinned to the exact versions it's tested with. The app needs 7 direct packages (26 including what they pull in):

| Package | Why |
|---|---|
| `fastapi`, `uvicorn`, `python-multipart` | the local web server, its API, and file uploads |
| `pandas`, `numpy` | reading and merging tables |
| `Pint` | unit parsing, conversion and dimension checks |
| `openpyxl` | Excel files |

FastAPI also pulls in `opentelemetry-api`, which is only an interface: without an OpenTelemetry SDK installed it does nothing and sends nothing.

Everything else is written in this repo with the Python standard library and plain browser JavaScript: the UI (no React, no build tools), the SVG charts (no matplotlib), file sniffing, the SQLite store (stdlib `sqlite3`), the watcher (stdlib threads), the pipeline runner, and the zip bundles. `requirements-dev.txt` adds `pytest` and `httpx` for the test suite only.

## Security model

Schemata is a **single-user app for your own computer**. Because pipeline steps can run Python scripts, it is locked down for local use:

- It listens on `127.0.0.1` only. `run.py` refuses other addresses unless you pass `--allow-remote`.
- It answers only requests addressed to `localhost`, which blocks DNS-rebinding attacks from websites.
- Every request that changes something must carry an `X-Schemata` header and come from Schemata's own page, so other sites can't trigger actions through your browser (CSRF).
- A strict Content-Security-Policy is set; generated SVGs are served sandboxed.
- Uploaded file names are sanitized, and paths can't escape the project folder.

There are no user accounts. Don't expose it to a network you don't trust.

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `--data` / `SCHEMATA_DATA_DIR` | `./data` | where projects and the database live |
| `--port` | `8000` | first port to try |
| `SCHEMATA_WATCH_INTERVAL` | `3` | seconds between watch-folder checks (`0` disables the watcher) |
| `SCHEMATA_MAX_UPLOAD_MB` | `200` | per-file upload limit |
| `SCHEMATA_ALLOWED_HOSTS` | (none) | extra host names to answer to (only for `--allow-remote` setups) |

## API

The UI uses a JSON API under `/api`. The OpenAPI spec is at `http://localhost:8000/openapi.json`. FastAPI's `/docs` pages are turned off because they load from a CDN. Requests that change state need the header `X-Schemata: 1`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health`, `/api/meta` | status; version, unit lists, templates, date formats |
| `GET` `POST` | `/api/projects` | list; create (`{name, template: lab \| blank \| sample}`) |
| `GET` `PATCH` `DELETE` | `/api/projects/{id}` | summary (also picks up new files); rename / auto-run; delete |
| `POST` | `/api/projects/{id}/files` | upload (multipart `files`) |
| `GET` `DELETE` | `/api/projects/{id}/files/{name}` | download original; remove |
| `GET` | `/api/projects/{id}/files/{name}/preview` | how the file was read, column profile, first rows |
| `GET` `PUT` | `/api/projects/{id}/schema` | target columns |
| `POST` | `/api/projects/{id}/schema/suggest` | suggested schema from the files |
| `GET` | `/api/projects/{id}/plan` | mappings, issues, value previews, approval state |
| `PUT` | `/api/projects/{id}/overrides` | save review decisions; returns the rebuilt plan |
| `POST` | `/api/projects/{id}/approve` | write `clean.py` and start a run |
| `GET` | `/api/projects/{id}/clean.py?draft=true` | the approved (or draft) script |
| `GET` | `/api/projects/{id}/pipeline` | graph, per-step status and logs, running state |
| `PUT` | `/api/projects/{id}/pipeline/steps` | configure analysis steps |
| `POST` | `/api/projects/{id}/pipeline/run` | `{force, only, wait}` |
| `GET` | `/api/projects/{id}/runs` | run history with time saved |
| `POST` | `/api/projects/{id}/scripts` | upload a step script |
| `GET` | `/api/projects/{id}/outputs[/{path}]` | list or fetch pipeline outputs |
| `GET` | `/api/projects/{id}/results` | merged rows (`offset`, `limit`, `q`, `sort`, `desc`) and column stats |
| `GET` | `/api/projects/{id}/download/{what}` | `merged.csv`, `merged.xlsx`, `clean.py`, `bundle.zip`, `activity.csv` |
| `GET` | `/api/projects/{id}/activity` | audit trail |

## Architecture

```
  Browser: plain HTML/CSS/JS (frontend/), served by the same process
        │  /api (JSON)
        ▼
  FastAPI (backend/main.py): security guard, routes
        │
        ├─ Workspace (workspace.py): projects, files, review, approval, runs, bundles
        │     ├─ Store (store.py): SQLite metadata + activity log + run history
        │     └─ Watcher (watcher.py): watch folders, auto-approve clean files, auto-run
        │
        ├─ MERGER (merger/)
        │     readers.py   detect separator/encoding/decimal/header row/sheet; read files
        │     profiler.py  column types, null rates, ranges
        │     schema.py    target columns, templates, validation
        │     suggest.py   schema suggestions from files
        │     headers.py   header → name + unit; rule → fuzzy matching
        │     units.py     pint conversion, case-aware unit tokens, cross-file inference
        │     dates.py     per-file date formats, ambiguity handling
        │     plan.py      plan + overrides, apply, value previews, validation
        │     codegen.py   standalone clean.py
        │
        └─ PIPELINE
              pipeline/          generic DAG, SHA-256 change detection, incremental executor
              project_pipeline.py  clean → summary/chart/script steps for a project
              pipeline_steps/    built-in steps: summary.py, chart.py, svgchart.py
```

**Design rule:** everything that touches your data is deterministic and inspectable. `match_header` accepts an optional `llm_matcher` callback for headers that rules and fuzzy matching can't resolve, but no LLM is connected and nothing leaves your machine.

## Repo structure

```
schemata/
├── run.py                 # start the app (checks Python version, opens the browser)
├── requirements.txt       # pinned runtime dependencies
├── requirements-dev.txt   # + test tools
├── backend/
│   ├── main.py            # FastAPI app: API routes, security guard, serves frontend/
│   ├── workspace.py       # application core
│   ├── store.py           # SQLite persistence
│   ├── watcher.py         # watch folders + auto-run
│   ├── project_pipeline.py
│   ├── demo_merge.py      # command-line merge of a folder (no server)
│   ├── merger/            # reading, matching, units, dates, plan, codegen, suggestions
│   ├── pipeline/          # DAG, hashing, executor
│   ├── pipeline_steps/    # built-in steps and the SVG chart renderer
│   └── tests/             # pytest: merger, pipeline, end-to-end API
├── frontend/              # index.html, styles.css, js/ (no build step)
├── sample_data/           # messy demo files + generator
└── data/                  # created on first run; your projects (gitignored)
```

## Development

```bash
python -m pip install -r requirements-dev.txt
cd backend
pytest -q                          # 52 tests: readers, units, plan, codegen, pipeline, API, auto-run
python demo_merge.py [folder]      # merge a folder from the command line → backend/output/
python ../sample_data/make_sample_data.py   # regenerate the sample files
```

The frontend is plain ES modules. Edit the files in `frontend/` and reload the page.

## Sample data

`sample_data/` has three files that record the same experiment in different ways:

| File | Temperature | Mass | Pressure | Dates |
|---|---|---|---|---|
| `lab_alice_2026-09.csv` | °C | mg | kPa | `09/01/2026 14:00` |
| `lab_bob_2026-09.csv` | K | g | atm | ISO 8601 |
| `sensor_carol_2026-09.xlsx` | unlabeled, **secretly °F** | g | kPa | `24-Sep-2026` |

## Demo script

1. **Try the sample project.** The Review tab flags Carol's unlabeled `Temp` column as °F (`74.2 → 23.44 °C`) and Alice's ambiguous dates.
2. Open **View clean.py**, then **Approve & run**. The pipeline graph lights up: clean → summary → two charts.
3. Turn on **Auto-run**, then copy a new month of data into the watch folder (the path is on the Files tab). Within seconds it's merged, the affected steps re-run, and the figures update.
4. Drop in a messy file (semicolon-separated, an unknown column). Auto-run waits and Review shows exactly why.
5. **Export bundle** gives you everything needed to reproduce the result, with hashes.

## Limitations

- Single user, local only; there are no accounts or sharing.
- Legacy `.xls` files aren't supported; save them as `.xlsx` or `.csv`.
- Files are processed in memory with pandas, which is comfortable up to a few hundred MB per project.
- The optional LLM header matcher isn't connected.

## Team

- Matthew Moser
- Denis Hoxhaj
