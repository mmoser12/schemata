"""Schemata API.

Flow for the frontend:
  1. POST /merge/preview         upload files -> profiles + proposed plan + session_id
  2. (user reviews/edits the plan in the UI)
  3. POST /merge/{id}/apply      send back the (edited) plan -> issues + preview rows
  4. GET  /merge/{id}/merged.csv and /merge/{id}/clean.py to download results

  5. GET  /pipeline               the demo DAG (clean -> analyze -> figures)
  6. GET  /pipeline/status        fresh/stale per step, no side effects
  7. POST /pipeline/run           run stale steps (or force a full run)
  8. GET  /pipeline/benchmark     full run timing vs. a no-op incremental run
  9. GET  /pipeline/figures/{name} the generated PNGs

Sessions live in memory for now; swap SESSIONS for Supabase later.
"""

from __future__ import annotations

import io
import json
import uuid
from typing import Any, Optional

import pandas as pd
from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response

from merger import TARGETS, MergePlan, apply_plan, build_plan, generate_clean_script, profile_file, read_table
from pipeline.demo import REPO_ROOT, build_demo_dag, ensure_clean_script, state_path
from pipeline.executor import Executor

app = FastAPI(title="Schemata API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# session_id -> {"tables": {name: DataFrame}, "plan": MergePlan, "merged": DataFrame | None}
SESSIONS: dict[str, dict[str, Any]] = {}

# The demo pipeline (clean -> analyze -> figures) is built once, lazily, the
# first time any /pipeline/* endpoint is hit.
_EXECUTOR: Optional[Executor] = None


def _executor() -> Executor:
    global _EXECUTOR
    if _EXECUTOR is None:
        ensure_clean_script()
        _EXECUTOR = Executor(build_demo_dag(), workdir=REPO_ROOT, state_path=state_path())
    return _EXECUTOR


def _records(df: pd.DataFrame, limit: int = 50) -> list[dict]:
    return json.loads(df.head(limit).to_json(orient="records", date_format="iso"))


def _session(session_id: str) -> dict:
    if session_id not in SESSIONS:
        raise HTTPException(404, "unknown session; upload files again")
    return SESSIONS[session_id]


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/targets")
def targets():
    return TARGETS


@app.post("/merge/preview")
async def merge_preview(files: list[UploadFile] = File(...)):
    tables: dict[str, pd.DataFrame] = {}
    for f in files:
        try:
            tables[f.filename] = read_table(io.BytesIO(await f.read()), filename=f.filename)
        except Exception as e:  # bad file shouldn't kill the whole upload
            raise HTTPException(400, f"could not read {f.filename}: {e}")

    plan = build_plan(tables)
    session_id = uuid.uuid4().hex[:12]
    SESSIONS[session_id] = {"tables": tables, "plan": plan, "merged": None}
    return {
        "session_id": session_id,
        "profiles": {name: profile_file(df) for name, df in tables.items()},
        "plan": plan.to_dict(),
    }


@app.post("/merge/{session_id}/apply")
def merge_apply(session_id: str, plan: Optional[dict] = Body(default=None)):
    """Apply the stored plan, or an edited plan sent in the body."""
    s = _session(session_id)
    if plan is not None:
        try:
            s["plan"] = MergePlan.from_dict(plan)
        except (KeyError, TypeError) as e:
            raise HTTPException(422, f"invalid plan: {e}")
    merged, validation = apply_plan(s["tables"], s["plan"])
    s["merged"] = merged
    return {
        "n_rows": len(merged),
        "columns": list(merged.columns),
        "rows": _records(merged),
        "issues": [i.__dict__ for i in [*s["plan"].issues, *validation]],
    }


@app.get("/merge/{session_id}/merged.csv")
def merged_csv(session_id: str):
    s = _session(session_id)
    if s["merged"] is None:
        raise HTTPException(409, "apply the plan first")
    return Response(s["merged"].to_csv(index=False), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=merged.csv"})


@app.get("/merge/{session_id}/clean.py", response_class=PlainTextResponse)
def clean_script(session_id: str):
    return generate_clean_script(_session(session_id)["plan"])


@app.get("/pipeline")
def pipeline_graph():
    """The DAG shape: steps, their declared files, and the derived edges."""
    return _executor().dag.describe()


@app.get("/pipeline/status")
def pipeline_status():
    """Fresh/stale per step, computed with no side effects (no execution)."""
    return [s.to_dict() for s in _executor().status()]


@app.post("/pipeline/run")
def pipeline_run(body: Optional[dict] = Body(default=None)):
    """Run every stale step (default), or force every step to re-run."""
    force = bool((body or {}).get("force", False))
    only = (body or {}).get("only")
    return _executor().run(force=force, only=only).to_dict()


@app.get("/pipeline/benchmark")
def pipeline_benchmark():
    """Force a full run, then an incremental no-op run; report both timings."""
    return _executor().benchmark()


@app.get("/pipeline/figures/{name}")
def pipeline_figure(name: str):
    path = REPO_ROOT / "backend" / "output" / "figures" / name
    if path.suffix.lower() != ".png" or ".." in name or "/" in name or "\\" in name:
        raise HTTPException(400, "invalid figure name")
    if not path.is_file():
        raise HTTPException(404, "figure not found; run the pipeline first")
    return FileResponse(path, media_type="image/png")
