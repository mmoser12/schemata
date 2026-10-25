"""The concrete demo pipeline: clean -> analyze -> figures.

This is "register your steps" from the README, done once for the sample_data
experiment. The merger's generated clean.py is step one automatically; two
small analysis steps make the matplotlib figures. `main.py` exposes this
through the /pipeline/* endpoints.

All paths are workdir-relative (the workdir is the repo root, so "clean"
can see sample_data/ and "analyze"/"figures" can see backend/output/). The
repo_root parameter exists so tests can point the whole thing at a throwaway
copy instead of the real sample_data/output.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .dag import Dag, Step

REPO_ROOT = Path(__file__).resolve().parents[2]


def _paths(repo_root: Path) -> dict[str, Path]:
    backend = repo_root / "backend"
    return {
        "backend": backend,
        "sample_data": repo_root / "sample_data",
        "output": backend / "output",
        "state": backend / "output" / "pipeline_state.json",
    }


def ensure_clean_script(repo_root: Path = REPO_ROOT) -> Path:
    """Make sure backend/output/clean.py exists, generating it once from
    sample_data if needed. This is the merger's output becoming step one."""
    p = _paths(repo_root)
    p["output"].mkdir(parents=True, exist_ok=True)
    clean_py = p["output"] / "clean.py"
    if clean_py.exists():
        return clean_py

    sys.path.insert(0, str(p["backend"]))
    from merger import build_plan, generate_clean_script, read_table  # noqa: E402

    files = sorted(f for f in p["sample_data"].iterdir() if f.suffix.lower() in {".csv", ".xlsx", ".xls", ".xlsm"})
    tables = {f.name: read_table(f) for f in files}
    plan = build_plan(tables)
    clean_py.write_text(generate_clean_script(plan))
    return clean_py


def build_demo_dag(repo_root: Path = REPO_ROOT) -> Dag:
    dag = Dag([
        Step(
            name="clean",
            title="Clean & merge",
            description="Runs the generated clean.py over sample_data to produce merged.csv.",
            command=["{python}", "backend/output/clean.py", "sample_data", "backend/output/merged.csv"],
            inputs=["sample_data/*.csv", "sample_data/*.xlsx", "backend/output/clean.py"],
            outputs=["backend/output/merged.csv"],
        ),
        Step(
            name="analyze",
            title="Analyze",
            description="Computes per-file summary statistics from merged.csv.",
            command=["{python}", "backend/pipeline_steps/analyze.py",
                      "backend/output/merged.csv", "backend/output/summary.csv"],
            inputs=["backend/output/merged.csv", "backend/pipeline_steps/analyze.py"],
            outputs=["backend/output/summary.csv"],
        ),
        Step(
            name="figures",
            title="Make figures",
            description="Renders matplotlib figures from merged.csv and summary.csv.",
            command=["{python}", "backend/pipeline_steps/figures.py", "backend/output/merged.csv",
                      "backend/output/summary.csv", "backend/output/figures"],
            inputs=["backend/output/merged.csv", "backend/output/summary.csv",
                    "backend/pipeline_steps/figures.py"],
            outputs=["backend/output/figures/temp_by_file.png", "backend/output/figures/timeseries.png"],
        ),
    ])
    dag.validate()
    return dag


def state_path(repo_root: Path = REPO_ROOT) -> Path:
    return _paths(repo_root)["state"]
