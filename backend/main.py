"""Schemata API + web UI (one process, no build step).

    python run.py                     # recommended: picks a port, opens the browser
    uvicorn main:app --port 8000      # from backend/, if you prefer

The UI is plain HTML/CSS/JS in ../frontend, served at /. The JSON API lives
under /api (OpenAPI spec at /openapi.json). Data goes to SCHEMATA_DATA_DIR
(default: <repo>/data).

Security model: Schemata is a local, single-user app that can run pipeline
scripts, so it only answers requests addressed to localhost (blocks DNS
rebinding), and every state-changing request must carry the X-Schemata header
and come from its own origin (blocks cross-site request forgery from other
tabs). Generated SVGs are served with a sandboxing CSP.
"""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import project_pipeline as pp
from merger import TEMPLATES, SchemaError
from merger import dates as dates_mod
from merger import units as units_mod
from watcher import Watcher
from workspace import REPO_ROOT, VERSION, Conflict, Invalid, NotFound, Workspace

FRONTEND = REPO_ROOT / "frontend"
_LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}
_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
_CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")


class ProjectCreate(BaseModel):
    name: str = Field("", max_length=200)
    template: str = "lab"
    description: str = Field("", max_length=1000)


class ProjectPatch(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    auto_run: Optional[bool] = None


class RunBody(BaseModel):
    force: bool = False
    only: Optional[list[str]] = None
    wait: bool = False


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()[:60] or "project"


def _attachment(data: Any, filename: str, media_type: str) -> Response:
    return Response(data, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def create_app(data_dir: Optional[Path] = None, watch_interval: Optional[float] = None,
               allowed_hosts: Optional[set[str]] = None) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("SCHEMATA_DATA_DIR") or REPO_ROOT / "data")
    if watch_interval is None:
        watch_interval = float(os.environ.get("SCHEMATA_WATCH_INTERVAL", "3"))
    extra_hosts = {h.strip().lower() for h in os.environ.get("SCHEMATA_ALLOWED_HOSTS", "").split(",") if h.strip()}
    hosts = (allowed_hosts or set()) | _LOOPBACK | extra_hosts | {"testserver"}

    ws = Workspace(data_dir)
    watcher = Watcher(ws, watch_interval)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        watcher.start()
        yield
        watcher.stop()

    # No /docs or /redoc: FastAPI serves those from a CDN, and Schemata makes no outbound requests.
    # The machine-readable spec is still at /openapi.json.
    app = FastAPI(title="Schemata API", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.workspace = ws
    app.state.watcher = watcher

    # ------------------------------------------------------------- security
    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").lower()
        hostname = host[:host.find("]") + 1] if host.startswith("[") else host.split(":")[0]
        if hostname not in hosts:
            return PlainTextResponse("Host not allowed. Schemata only answers on localhost "
                                     "(set SCHEMATA_ALLOWED_HOSTS to change this).", status_code=400)
        if request.method in _UNSAFE and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if request.headers.get("x-schemata") != "1" or (origin and origin.split("://", 1)[-1].lower() != host):
                return JSONResponse({"detail": "cross-site request blocked"}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if not request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    @app.exception_handler(NotFound)
    async def _nf(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=404)

    @app.exception_handler(Conflict)
    async def _cf(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.exception_handler(Invalid)
    async def _iv(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(SchemaError)
    async def _se(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=422)

    # ----------------------------------------------------------------- meta
    @app.get("/api/health")
    def health():
        return {"ok": True, "version": VERSION}

    @app.get("/api/meta")
    def meta():
        return {
            "version": VERSION,
            "data_dir": str(data_dir),
            "templates": {k: {"label": v["label"], "description": v["description"]} for k, v in TEMPLATES.items()},
            "units": units_mod.unit_groups(),
            "date_formats": dates_mod.CANDIDATE_FORMATS,
            "kinds": ["id", "datetime", "quantity", "number", "text"],
            "steps": pp.step_kinds(),
            "auto_run_interval_s": watch_interval,
        }

    @app.get("/api/units/check")
    def unit_check(u: str, target: Optional[str] = None):
        try:
            unit = units_mod.parse_unit(u)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        out = {"ok": True, "unit": unit, "label": units_mod.pretty(unit), "dimension": units_mod.dimension_name(unit)}
        if target:
            try:
                out["compatible"] = units_mod.same_dimension(unit, units_mod.parse_unit(target))
            except ValueError:
                out["compatible"] = False
        return out

    # ------------------------------------------------------------- projects
    @app.get("/api/projects")
    def projects():
        return ws.list_projects()

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectCreate):
        return ws.create_project(body.name, body.template, body.description)

    @app.get("/api/projects/{pid}")
    def project(pid: str):
        return ws.summary(pid)

    @app.patch("/api/projects/{pid}")
    def patch_project(pid: str, body: ProjectPatch):
        return ws.update_project(pid, body.name, body.description, body.auto_run)

    @app.delete("/api/projects/{pid}", status_code=204)
    def delete_project(pid: str):
        ws.delete_project(pid)
        return Response(status_code=204)

    # ---------------------------------------------------------------- files
    @app.post("/api/projects/{pid}/files")
    async def upload(pid: str, files: list[UploadFile] = File(...)):
        uploads = [(f.filename or "", await f.read()) for f in files]
        return ws.add_files(pid, uploads)

    @app.get("/api/projects/{pid}/files/{name}/preview")
    def file_preview(pid: str, name: str):
        return ws.file_preview(pid, name)

    @app.get("/api/projects/{pid}/files/{name}")
    def file_download(pid: str, name: str):
        path = ws.raw_path(pid, name)
        return FileResponse(path, filename=path.name)

    @app.delete("/api/projects/{pid}/files/{name}", status_code=204)
    def file_delete(pid: str, name: str):
        ws.delete_file(pid, name)
        return Response(status_code=204)

    # --------------------------------------------------------------- schema
    @app.get("/api/projects/{pid}/schema")
    def get_schema(pid: str):
        ws.project(pid)
        from merger.schema import to_json
        return {"targets": to_json(ws.targets(pid))}

    @app.put("/api/projects/{pid}/schema")
    def put_schema(pid: str, body: dict = Body(...)):
        return {"targets": ws.set_targets(pid, body.get("targets", {}))}

    @app.post("/api/projects/{pid}/schema/suggest")
    def suggest(pid: str):
        return ws.suggest_schema(pid)

    # ----------------------------------------------------------- plan/review
    @app.get("/api/projects/{pid}/plan")
    def get_plan(pid: str):
        ws.sync_files(pid)
        return ws.plan_json(pid)

    @app.put("/api/projects/{pid}/overrides")
    def put_overrides(pid: str, body: dict = Body(...)):
        ws.set_overrides(pid, body.get("overrides", {}))
        return ws.plan_json(pid)

    @app.post("/api/projects/{pid}/approve")
    def approve(pid: str):
        return ws.approve(pid)

    @app.get("/api/projects/{pid}/clean.py", response_class=PlainTextResponse)
    def clean_py(pid: str, draft: bool = False):
        return ws.clean_script(pid, approved=not draft)

    # ------------------------------------------------------------- pipeline
    @app.get("/api/projects/{pid}/pipeline")
    def pipeline(pid: str):
        return ws.pipeline(pid)

    @app.put("/api/projects/{pid}/pipeline/steps")
    def put_steps(pid: str, body: dict = Body(...)):
        return ws.set_steps(pid, body.get("steps", []))

    @app.post("/api/projects/{pid}/pipeline/run")
    def run(pid: str, body: Optional[RunBody] = None):
        body = body or RunBody()
        return ws.start_run(pid, trigger="manual", force=body.force, only=body.only, wait=body.wait)

    @app.get("/api/projects/{pid}/runs")
    def runs(pid: str):
        return ws.runs(pid)

    @app.post("/api/projects/{pid}/scripts")
    async def upload_script(pid: str, file: UploadFile = File(...)):
        return ws.save_script(pid, file.filename or "", await file.read())

    @app.get("/api/projects/{pid}/outputs")
    def outputs(pid: str):
        return ws.outputs(pid)

    @app.get("/api/projects/{pid}/outputs/{rel:path}")
    def output_file(pid: str, rel: str, download: bool = False):
        path = ws.output_path(pid, rel)
        headers = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="{path.name}"'
        media = "image/svg+xml" if path.suffix.lower() == ".svg" else None
        return FileResponse(path, media_type=media, headers=headers)

    # -------------------------------------------------------------- results
    @app.get("/api/projects/{pid}/results")
    def results(pid: str, offset: int = 0, limit: int = 100, q: str = "", sort: str = "", desc: bool = False):
        return ws.results(pid, max(0, offset), limit, q[:200], sort, desc)

    @app.get("/api/projects/{pid}/download/{what}")
    def download(pid: str, what: str):
        p = ws.project(pid)
        slug = _slug(p["name"])
        if what == "merged.csv":
            path = ws.paths(pid).merged
            if not path.exists():
                raise HTTPException(404, "run the pipeline first")
            return FileResponse(path, media_type="text/csv", filename=f"{slug}-merged.csv")
        if what == "merged.xlsx":
            return _attachment(ws.merged_xlsx(pid), f"{slug}-merged.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        if what == "clean.py":
            return _attachment(ws.clean_script(pid), "clean.py", "text/x-python")
        if what == "bundle.zip":
            return _attachment(ws.bundle(pid), f"{slug}-bundle.zip", "application/zip")
        if what == "activity.csv":
            return _attachment(ws.activity_csv(pid), f"{slug}-activity.csv", "text/csv")
        raise HTTPException(404, "unknown download")

    # ------------------------------------------------------------- activity
    @app.get("/api/projects/{pid}/activity")
    def activity(pid: str, limit: int = 300, before: Optional[int] = None):
        return ws.activity(pid, limit=max(1, min(limit, 1000)), before=before)

    # ------------------------------------------------------------------- UI
    if FRONTEND.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="ui")

    return app


app = create_app()
