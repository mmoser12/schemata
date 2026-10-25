"""The application core: projects, files, schema, review, approval, runs.

Everything the API does goes through Workspace. Lifecycle of a project:

  1. files land in raw/ (upload, or dropped into the folder directly)
  2. sync_files() notices them, hashes them and logs the change
  3. plan() rebuilds the merge plan from data + schema + overrides
  4. the user reviews; approve() freezes that plan into clean.py
  5. start_run() executes the pipeline in the background, re-running only
     what's stale, and records the run
  6. with auto-run on, the watcher does 2-5 by itself whenever a new file maps
     cleanly (no warnings), and waits for review otherwise
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import platform
import re
import shutil
import sys
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pandas as pd

import project_pipeline as pp
from merger import (SUPPORTED_SUFFIXES, UnsupportedFile, apply_plan, build_plan, column_previews,
                    describe_options, generate_clean_script, head_records, load, normalize_targets,
                    profile_file, same_script, suggest_targets)
from merger import schema as schema_mod
from merger import units as units_mod
from merger.plan import Issue
from pipeline.executor import Executor
from pipeline.hashing import PipelineState, file_hash
from store import Store

VERSION = "1.0.0"
REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "sample_data"
MAX_UPLOAD_BYTES = int(os.environ.get("SCHEMATA_MAX_UPLOAD_MB", "200")) * 1024 * 1024
MAX_SCRIPT_BYTES = 1024 * 1024
_FILE_NAME = re.compile(r"^[^/\\:*?\"<>|\x00-\x1f]{1,200}$")


class NotFound(LookupError):
    pass


class Conflict(RuntimeError):
    """The request can't be done in the project's current state (HTTP 409)."""


class Invalid(ValueError):
    """The request itself is wrong (HTTP 422)."""


def safe_file_name(name: str) -> str:
    name = Path(str(name or "")).name.strip()
    if not name or name.startswith(".") or not _FILE_NAME.match(name):
        raise Invalid(f"'{name}' isn't a usable file name")
    return name


@dataclass
class Paths:
    root: Path

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def output(self) -> Path:
        return self.root / "output"

    @property
    def clean_py(self) -> Path:
        return self.root / "clean.py"

    @property
    def merged(self) -> Path:
        return self.output / "merged.csv"

    @property
    def state(self) -> Path:
        return self.output / "pipeline_state.json"

    @property
    def scripts(self) -> Path:
        return self.root / "scripts"

    def json(self, name: str) -> Path:
        return self.root / f"{name}.json"


@dataclass
class RunState:
    running: bool = False
    step: Optional[str] = None
    trigger: Optional[str] = None
    started_at: Optional[float] = None
    error: Optional[str] = None
    last_report: Optional[dict] = None


@dataclass
class _Cache:
    tables: dict = field(default_factory=dict)  # (path, sha) -> (df, options)
    merged: dict = field(default_factory=dict)  # path -> (mtime_ns, df)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))  # key order matters (schema column order)
    tmp.replace(path)


def _config_fingerprint(targets: dict, overrides: dict) -> str:
    blob = json.dumps({"t": schema_mod.to_json(targets), "o": overrides}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


class Workspace:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.projects_dir = self.data_dir / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.data_dir / "schemata.db")
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._runs: dict[str, RunState] = {}
        self._cache = _Cache()

    # ------------------------------------------------------------------ basics
    def lock(self, pid: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(pid, threading.RLock())

    def paths(self, pid: str) -> Paths:
        if not re.fullmatch(r"[0-9a-f]{12}", pid or ""):
            raise NotFound("unknown project")
        return Paths(self.projects_dir / pid)

    def project(self, pid: str) -> dict:
        p = self.store.project(pid) if re.fullmatch(r"[0-9a-f]{12}", pid or "") else None
        if p is None:
            raise NotFound("unknown project")
        return p

    def run_state(self, pid: str) -> RunState:
        return self._runs.setdefault(pid, RunState())

    # ---------------------------------------------------------------- projects
    def list_projects(self) -> list[dict]:
        out = []
        for p in self.store.projects():
            files = self.store.files(p["id"])
            out.append({**self._project_meta(p), "n_files": len(files),
                        "n_unapproved": sum(f["approved_sha256"] != f["sha256"] for f in files)})
        return out

    def create_project(self, name: str, template: str = "lab", description: str = "") -> dict:
        name = (name or "").strip()[:120] or "Untitled project"
        if template not in ("lab", "blank", "sample"):
            raise Invalid("template must be lab, blank or sample")
        pid = uuid.uuid4().hex[:12]
        paths = self.paths(pid)
        for d in (paths.raw, paths.output, paths.scripts, paths.root / "steps"):
            d.mkdir(parents=True, exist_ok=True)
        targets = schema_mod.from_template("blank" if template == "blank" else "lab")
        _write_json(paths.json("schema"), schema_mod.to_json(targets))
        _write_json(paths.json("overrides"), {})
        steps = pp.validate_steps(pp.default_steps(targets, sample=template == "sample"), targets, paths.scripts)
        _write_json(paths.json("pipeline"), {"steps": steps})
        self.store.create_project(pid, name, description[:500])
        self.store.log(pid, "project", f"Created project '{name}'", template=template)

        if template == "sample":
            for f in sorted(SAMPLE_DIR.iterdir()):
                if f.suffix.lower() in SUPPORTED_SUFFIXES:
                    shutil.copy2(f, paths.raw / f.name)
            self.sync_files(pid)
        return self.summary(pid)

    def update_project(self, pid: str, name: Optional[str] = None, description: Optional[str] = None,
                       auto_run: Optional[bool] = None) -> dict:
        p = self.project(pid)
        fields: dict = {}
        if name is not None and name.strip() and name.strip() != p["name"]:
            fields["name"] = name.strip()[:120]
            self.store.log(pid, "project", f"Renamed project to '{fields['name']}'", previous=p["name"])
        if description is not None and description != p["description"]:
            fields["description"] = description[:500]
        if auto_run is not None and bool(auto_run) != bool(p["auto_run"]):
            fields["auto_run"] = int(bool(auto_run))
            self.store.log(pid, "project", f"Auto-run turned {'on' if auto_run else 'off'}")
        if fields:
            self.store.update_project(pid, **fields)
        return self.summary(pid)

    def delete_project(self, pid: str) -> None:
        self.project(pid)
        if self.run_state(pid).running:
            raise Conflict("a pipeline run is in progress; wait for it to finish")
        with self.lock(pid):
            self.store.delete_project(pid)
            shutil.rmtree(self.paths(pid).root, ignore_errors=True)

    def _project_meta(self, p: dict) -> dict:
        return {"id": p["id"], "name": p["name"], "description": p["description"],
                "created_at": p["created_at"], "updated_at": p["updated_at"], "auto_run": bool(p["auto_run"])}

    # ------------------------------------------------------------------ files
    def sync_files(self, pid: str) -> list[dict]:
        """Reconcile raw/ with the database. Returns the changes it found."""
        self.project(pid)
        paths = self.paths(pid)
        changes = []
        with self.lock(pid):
            known = {f["name"]: f for f in self.store.files(pid)}
            present = {}
            for f in sorted(paths.raw.iterdir()) if paths.raw.is_dir() else []:
                if f.is_file() and not f.name.startswith((".", "~$")) and f.suffix.lower() in SUPPORTED_SUFFIXES:
                    present[f.name] = f
            for name, f in present.items():
                st = f.stat()
                rec = known.get(name)
                if rec and rec["size"] == st.st_size and rec["mtime_ns"] == st.st_mtime_ns:
                    continue
                sha = file_hash(f)
                if rec and rec["sha256"] == sha:
                    self.store.upsert_file(pid, name, sha, st.st_size, st.st_mtime_ns)
                    continue
                self.store.upsert_file(pid, name, sha, st.st_size, st.st_mtime_ns)
                kind = "changed" if rec else "added"
                changes.append({"file": name, "change": kind})
                self.store.log(pid, "file", f"{'Detected change in' if rec else 'Added'} {name}",
                               sha256=sha, size=st.st_size, previous_sha256=rec["sha256"] if rec else None)
            for name in set(known) - set(present):
                self.store.delete_file(pid, name)
                changes.append({"file": name, "change": "removed"})
                self.store.log(pid, "file", f"{name} was removed", previous_sha256=known[name]["sha256"])
        return changes

    def add_files(self, pid: str, uploads: list[tuple[str, bytes]]) -> dict:
        self.project(pid)
        paths = self.paths(pid)
        saved, skipped, rejected = [], [], []
        with self.lock(pid):
            for raw_name, data in uploads:
                try:
                    name = safe_file_name(raw_name)
                    if Path(name).suffix.lower() == ".xls":
                        raise UnsupportedFile("legacy .xls files aren't supported; save the sheet as .xlsx or .csv")
                    if Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
                        raise UnsupportedFile("use .csv, .tsv, .txt, .xlsx or .xlsm")
                    if len(data) > MAX_UPLOAD_BYTES:
                        raise UnsupportedFile(f"larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
                    if not data:
                        raise UnsupportedFile("the file is empty")
                except (Invalid, UnsupportedFile) as e:
                    rejected.append({"file": raw_name, "reason": str(e)})
                    continue
                target = paths.raw / name
                if target.exists() and target.read_bytes() == data:
                    skipped.append(name)
                    continue
                # Check it's readable before it lands in raw/ (and the pipeline's inputs).
                tmp = paths.root / f".upload-{uuid.uuid4().hex}{Path(name).suffix}"
                tmp.write_bytes(data)
                try:
                    load(tmp)
                except Exception as e:  # pandas/openpyxl raise many types for broken files
                    tmp.unlink(missing_ok=True)
                    rejected.append({"file": name, "reason": f"couldn't be read: {e}"})
                    continue
                tmp.replace(target)
                saved.append(name)
            changes = self.sync_files(pid)
        return {"saved": saved, "unchanged": skipped, "rejected": rejected, "changes": changes}

    def delete_file(self, pid: str, name: str) -> None:
        self.project(pid)
        name = safe_file_name(name)
        with self.lock(pid):
            path = self.paths(pid).raw / name
            if not path.is_file():
                raise NotFound(f"no file called {name}")
            path.unlink()
            self.sync_files(pid)

    def raw_path(self, pid: str, name: str) -> Path:
        self.project(pid)
        path = self.paths(pid).raw / safe_file_name(name)
        if not path.is_file():
            raise NotFound("file not found")
        return path

    def _tables(self, pid: str) -> tuple[dict, dict, list[Issue]]:
        """Load every raw file (cached by content hash). Returns (tables, read_options, read_errors)."""
        paths = self.paths(pid)
        tables, options, errors = {}, {}, []
        for f in self.store.files(pid):
            path = paths.raw / f["name"]
            key = (str(path), f["sha256"])
            if key not in self._cache.tables:
                try:
                    self._cache.tables[key] = load(path)
                except Exception as e:
                    errors.append(Issue("error", f["name"], None, f"couldn't read this file: {e}", "general"))
                    continue
                if len(self._cache.tables) > 256:
                    self._cache.tables.pop(next(iter(self._cache.tables)))
            df, opts = self._cache.tables[key]
            tables[f["name"]], options[f["name"]] = df, opts
        return tables, options, errors

    def file_preview(self, pid: str, name: str) -> dict:
        self.project(pid)
        tables, options, _ = self._tables(pid)
        if name not in tables:
            raise NotFound("file not found or unreadable")
        df = tables[name]
        return {"file": name, "read_options": options[name], "read_as": describe_options(options[name]),
                "profile": profile_file(df), **head_records(df, 25)}

    # ----------------------------------------------------------------- schema
    def targets(self, pid: str) -> dict:
        return schema_mod.from_json(_read_json(self.paths(pid).json("schema"), {}))

    def set_targets(self, pid: str, targets: dict) -> dict:
        self.project(pid)
        try:
            clean = normalize_targets(schema_mod.from_json(targets))
        except schema_mod.SchemaError as e:
            raise Invalid(str(e))
        with self.lock(pid):
            before = self.targets(pid)
            _write_json(self.paths(pid).json("schema"), schema_mod.to_json(clean))
            added = sorted(set(clean) - set(before))
            removed = sorted(set(before) - set(clean))
            changed = sorted(k for k in set(clean) & set(before) if clean[k] != before[k])
            bits = [f"added {', '.join(added)}" if added else "", f"removed {', '.join(removed)}" if removed else "",
                    f"changed {', '.join(changed)}" if changed else ""]
            self.store.log(pid, "schema", "Schema updated: " + ("; ".join(b for b in bits if b) or "no changes"),
                           previous=schema_mod.to_json(before), current=schema_mod.to_json(clean))
            self._prune_steps(pid, clean)
        return schema_mod.to_json(clean)

    def _prune_steps(self, pid: str, targets: dict) -> None:
        """Drop chart/summary settings that point at columns that no longer exist."""
        steps = self.steps(pid)
        kept = []
        for s in steps:
            try:
                kept.extend(pp.validate_steps([s], targets, self.paths(pid).scripts))
            except pp.StepError:
                self.store.log(pid, "step", f"Removed step '{s['name']}': its columns aren't in the schema anymore")
        if len(kept) != len(steps):
            _write_json(self.paths(pid).json("pipeline"), {"steps": kept})

    def suggest_schema(self, pid: str) -> dict:
        self.project(pid)
        tables, _, _ = self._tables(pid)
        if not tables:
            raise Conflict("add some files first; suggestions come from their columns")
        targets, notes = suggest_targets(tables)
        return {"targets": schema_mod.to_json(targets), "notes": notes}

    # -------------------------------------------------------------- overrides
    def overrides(self, pid: str) -> dict:
        return _read_json(self.paths(pid).json("overrides"), {})

    def set_overrides(self, pid: str, overrides: dict) -> dict:
        self.project(pid)
        if not isinstance(overrides, dict):
            raise Invalid("overrides must be an object")
        clean: dict = {}
        for fname, cols in overrides.items():
            if not isinstance(cols, dict):
                raise Invalid("overrides must map file -> column -> settings")
            for col, ov in cols.items():
                if not isinstance(ov, dict):
                    raise Invalid("each override must be an object")
                entry = {k: ov[k] for k in ("target", "unit", "date_format") if k in ov}
                if "target" in entry and entry["target"] is not None and not isinstance(entry["target"], str):
                    raise Invalid("target must be a column name or null")
                for k in ("unit", "date_format"):
                    if k in entry and not entry[k]:
                        entry.pop(k)
                if entry:
                    clean.setdefault(str(fname), {})[str(col)] = entry
        with self.lock(pid):
            before = self.overrides(pid)
            _write_json(self.paths(pid).json("overrides"), clean)
            for change in _override_changes(before, clean):
                message = change.pop("message")
                self.store.log(pid, "mapping", message, **change)
        return self.plan(pid)

    # ------------------------------------------------------------------- plan
    def plan(self, pid: str) -> dict:
        """The current merge plan with every issue, value previews and approval state."""
        self.project(pid)
        with self.lock(pid):
            targets = self.targets(pid)
            overrides = self.overrides(pid)
            tables, options, read_errors = self._tables(pid)
            files = {f["name"]: f for f in self.store.files(pid)}
            unapproved = [n for n in tables if files[n]["approved_sha256"] != files[n]["sha256"]]
            plan = build_plan(tables, targets, overrides=overrides, read_options=options)
            approved = [n for n in tables if n not in unapproved]
            issues = _with_drift(plan, unapproved, approved)
            merged, validation = apply_plan(tables, plan) if tables else (None, [])
            issues = [*read_errors, *issues, *validation]
            script = generate_clean_script(plan)
            current = self.paths(pid).clean_py.read_text() if self.paths(pid).clean_py.exists() else ""
            config_changed = (self.project(pid)["approved_config"] != _config_fingerprint(targets, overrides))
            return {
                "plan": plan,
                "issues": issues,
                "tables": tables,
                "previews": column_previews(tables, plan),
                "read_options": {n: {"options": o, "summary": describe_options(o)} for n, o in options.items()},
                "unapproved": unapproved,
                "read_errors": [i.file for i in read_errors],
                "needs_approval": bool(tables) and (bool(unapproved) or not same_script(script, current)),
                "config_changed": config_changed,
                "script": script,
                "merged_rows": 0 if merged is None else len(merged),
            }

    def plan_json(self, pid: str) -> dict:
        p = self.plan(pid)
        target_info = {}
        for name, spec in p["plan"].targets.items():
            info = {"kind": spec["kind"]}
            if spec.get("unit"):
                info.update(unit_label=units_mod.pretty(spec["unit"]), dimension=units_mod.dimension_name(spec["unit"]))
            target_info[name] = info
        examples = {f: {str(c): [str(v) for v in df[c].dropna().head(3).tolist()] for c in df.columns}
                    for f, df in p["tables"].items()}
        return {
            "target_info": target_info,
            "examples": examples,
            "plan": p["plan"].to_dict() | {"targets": schema_mod.to_json(p["plan"].targets)},
            "issues": [i.__dict__ for i in p["issues"]],
            "previews": p["previews"],
            "read_options": p["read_options"],
            "overrides": self.overrides(pid),
            "unapproved": p["unapproved"],
            "needs_approval": p["needs_approval"],
            "merged_rows": p["merged_rows"],
            "counts": _count(p["issues"]),
        }

    def clean_script(self, pid: str, approved: bool = True) -> str:
        if approved and self.paths(pid).clean_py.exists():
            return self.paths(pid).clean_py.read_text()
        return self.plan(pid)["script"]

    # ---------------------------------------------------------------- approve
    def approve(self, pid: str, trigger: str = "manual", run: bool = True) -> dict:
        self.project(pid)
        with self.lock(pid):
            if self.run_state(pid).running:
                raise Conflict("a pipeline run is in progress; approve again when it finishes")
            p = self.plan(pid)
            if not p["tables"]:
                raise Conflict("there are no files to merge yet")
            if p["read_errors"]:
                raise Conflict(f"fix or remove unreadable file(s) first: {', '.join(p['read_errors'])}")
            paths = self.paths(pid)
            if not paths.clean_py.exists() or not same_script(p["script"], paths.clean_py.read_text()):
                paths.clean_py.write_text(p["script"])
            self.store.approve_files(pid)
            fingerprint = _config_fingerprint(self.targets(pid), self.overrides(pid))
            self.store.update_project(pid, approved_config=fingerprint)
            counts = _count(p["issues"])
            how = "Auto-approved" if trigger == "auto" else "Approved"
            self.store.log(pid, "approve", f"{how} the merge of {len(p['tables'])} file(s) "
                           f"({counts['error']} errors, {counts['warning']} warnings)",
                           files=sorted(p["tables"]), clean_py_sha256=hashlib.sha256(
                               paths.clean_py.read_bytes()).hexdigest())
        if run:
            self.start_run(pid, trigger=trigger)
        return self.summary(pid)

    # --------------------------------------------------------------- pipeline
    def steps(self, pid: str) -> list[dict]:
        return _read_json(self.paths(pid).json("pipeline"), {"steps": []}).get("steps", [])

    def set_steps(self, pid: str, steps: list) -> dict:
        self.project(pid)
        try:
            clean = pp.validate_steps(steps, self.targets(pid), self.paths(pid).scripts)
            pp.build_dag(self.paths(pid).root, clean, self.targets(pid))  # catches duplicate outputs, cycles
        except pp.StepError as e:
            raise Invalid(str(e))
        with self.lock(pid):
            before = {s["name"]: s for s in self.steps(pid)}
            _write_json(self.paths(pid).json("pipeline"), {"steps": clean})
            after = {s["name"]: s for s in clean}
            for n in sorted(set(after) - set(before)):
                self.store.log(pid, "step", f"Added step '{n}' ({after[n]['kind']})", step=after[n])
            for n in sorted(set(before) - set(after)):
                self.store.log(pid, "step", f"Removed step '{n}'", previous=before[n])
            for n in sorted(set(after) & set(before)):
                if after[n] != before[n]:
                    self.store.log(pid, "step", f"Changed step '{n}'", previous=before[n], step=after[n])
        return self.pipeline(pid)

    def save_script(self, pid: str, name: str, data: bytes) -> dict:
        self.project(pid)
        name = safe_file_name(name)
        if not re.match(r"^[A-Za-z0-9_.-]+\.py$", name):
            raise Invalid("scripts must be .py files with simple names (letters, digits, _ . -)")
        if len(data) > MAX_SCRIPT_BYTES:
            raise Invalid("script is larger than 1 MB")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise Invalid("script must be UTF-8 text")
        path = self.paths(pid).scripts / name
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = file_hash(path) if path.exists() else None
        path.write_bytes(data)
        self.store.log(pid, "step", f"{'Updated' if previous else 'Uploaded'} script {name}",
                       sha256=hashlib.sha256(data).hexdigest(), previous_sha256=previous)
        return {"scripts": self.scripts(pid)}

    def scripts(self, pid: str) -> list[str]:
        d = self.paths(pid).scripts
        return sorted(f.name for f in d.glob("*.py")) if d.is_dir() else []

    def _executor(self, pid: str) -> Executor:
        paths = self.paths(pid)
        dag = pp.build_dag(paths.root, self.steps(pid), self.targets(pid))
        return Executor(dag, workdir=paths.root, state_path=paths.state)

    def pipeline(self, pid: str) -> dict:
        self.project(pid)
        with self.lock(pid):
            ex = self._executor(pid)
            statuses = {s.step: s.to_dict() for s in ex.status()}
            state = PipelineState.load(self.paths(pid).state)
        rs = self.run_state(pid)
        steps_cfg = {s["name"]: s for s in self.steps(pid)}
        graph = ex.dag.describe()
        for s in graph["steps"]:
            rec = state.get(s["name"])
            s["status"] = statuses[s["name"]]
            s["state"] = ("running" if rs.running and rs.step == s["name"] else
                          "failed" if rec.status == "failed" else
                          "fresh" if statuses[s["name"]]["fresh"] else
                          "never_run" if rec.status == "never_run" else "stale")
            s["config"] = steps_cfg.get(s["name"])
            s["last"] = {"returncode": rec.returncode, "stdout": rec.stdout_tail, "stderr": rec.stderr_tail,
                         "ran_at": rec.ran_at, "duration_s": rec.duration_s}
            s["command"] = [Path(a).name if a.startswith(str(pp.STEPS_CODE)) else a for a in s["command"]]
            s["command"] = ["python" if a == "{python}" else a for a in s["command"]]
        return {
            "graph": graph,
            "running": rs.running, "current_step": rs.step, "trigger": rs.trigger,
            "started_at": rs.started_at, "error": rs.error,
            "last_report": rs.last_report,
            "stale": [n for n, st in statuses.items() if not st["fresh"]],
            "scripts": self.scripts(pid),
            "kinds": pp.step_kinds(),
        }

    def start_run(self, pid: str, trigger: str = "manual", force: bool = False,
                  only: Optional[list[str]] = None, wait: bool = False) -> dict:
        self.project(pid)
        rs = self.run_state(pid)
        with self.lock(pid):
            if rs.running:
                raise Conflict("a run is already in progress")
            p = self.plan(pid)
            if not p["tables"]:
                raise Conflict("add files and approve the merge before running the pipeline")
            if p["needs_approval"] or not self.paths(pid).clean_py.exists():
                raise Conflict("review and approve the merge first: the files or mapping changed since the "
                               "last approval")
            ex = self._executor(pid)
            if only:
                unknown = [n for n in only if n not in ex.dag]
                if unknown:
                    raise Invalid(f"unknown step(s): {', '.join(unknown)}")
            rs.running, rs.step, rs.trigger, rs.started_at, rs.error = True, None, trigger, time.time(), None

        def work() -> None:
            try:
                state = PipelineState.load(self.paths(pid).state)
                env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
                       "MPLCONFIGDIR": str(self.data_dir / ".cache" / "matplotlib")}
                report = ex.run(force=force, only=only, on_step=lambda n: setattr(rs, "step", n), env=env)
                saved = sum(state.get(n).duration_s for n in report.skipped)
                d = report.to_dict()
                d["time_saved_s"] = saved
                self.store.add_run(pid, trigger, d, saved)
                rs.last_report = d
                msg = (f"{'Full' if force else 'Incremental'} run ({trigger}): ran {len(report.ran)}, "
                       f"skipped {len(report.skipped)}")
                if report.failed:
                    msg += f", FAILED {', '.join(report.failed)}"
                self.store.log(pid, "run", msg, ran=report.ran, skipped=report.skipped, failed=report.failed,
                               duration_s=round(report.total_duration_s, 3), time_saved_s=round(saved, 3))
            except Exception as e:  # surface anything unexpected to the UI instead of dying silently
                rs.error = str(e)
                self.store.log(pid, "run", f"Run crashed: {e}")
            finally:
                rs.running, rs.step = False, None

        if wait:
            work()
        else:
            threading.Thread(target=work, name=f"run-{pid}", daemon=True).start()
        return self.pipeline(pid)

    def runs(self, pid: str) -> list[dict]:
        self.project(pid)
        return self.store.runs(pid)

    def output_path(self, pid: str, rel: str) -> Path:
        self.project(pid)
        out = self.paths(pid).output.resolve()
        path = (out / rel).resolve()
        if out not in path.parents or not path.is_file() or path.name == "pipeline_state.json":
            raise NotFound("output not found")
        return path

    def outputs(self, pid: str) -> list[dict]:
        out = self.paths(pid).output
        files = []
        for f in sorted(out.rglob("*")) if out.is_dir() else []:
            if f.is_file() and f.name != "pipeline_state.json" and not f.name.startswith("."):
                files.append({"path": str(f.relative_to(out).as_posix()), "size": f.stat().st_size,
                              "modified": f.stat().st_mtime})
        return files

    # ---------------------------------------------------------------- results
    def _merged(self, pid: str) -> Optional[pd.DataFrame]:
        path = self.paths(pid).merged
        if not path.exists():
            return None
        mtime = path.stat().st_mtime_ns
        hit = self._cache.merged.get(str(path))
        if hit and hit[0] == mtime:
            return hit[1]
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        self._cache.merged[str(path)] = (mtime, df)
        return df

    def results(self, pid: str, offset: int = 0, limit: int = 100, q: str = "", sort: str = "",
                desc: bool = False) -> dict:
        self.project(pid)
        df = self._merged(pid)
        targets = self.targets(pid)
        if df is None:
            return {"available": False, "columns": [], "rows": [], "total": 0, "filtered": 0, "stats": {}}
        view = df
        if q:
            mask = pd.Series(False, index=df.index)
            for c in df.columns:
                mask |= df[c].str.contains(q, case=False, regex=False, na=False)
            view = df[mask]
        if sort and sort in df.columns:
            nums = pd.to_numeric(view[sort].where(view[sort] != ""), errors="coerce")
            key = nums if nums.notna().sum() >= max(1, len(view) // 2) else view[sort].str.lower()
            view = view.loc[key.sort_values(ascending=not desc, na_position="last").index]
        limit = max(1, min(limit, 1000))
        page = view.iloc[offset:offset + limit]
        stats = {}
        for c in df.columns:
            nums = pd.to_numeric(df[c].where(df[c] != ""), errors="coerce")
            empty = int((df[c] == "").sum())
            if c in targets and targets[c]["kind"] in ("quantity", "number") and nums.notna().any():
                stats[c] = {"type": "numeric", "count": int(nums.notna().sum()), "empty": empty,
                            "min": float(nums.min()), "max": float(nums.max()),
                            "mean": float(nums.mean()), "median": float(nums.median())}
            else:
                stats[c] = {"type": "text", "count": int((df[c] != "").sum()), "empty": empty,
                            "unique": int(df[c][df[c] != ""].nunique())}
        units_by_col = {c: (targets[c].get("unit") if c in targets else None) for c in df.columns}
        return {"available": True, "columns": list(df.columns), "rows": page.values.tolist(),
                "total": len(df), "filtered": len(view), "offset": offset, "stats": stats,
                "units": units_by_col, "kinds": {c: targets.get(c, {}).get("kind", "text") for c in df.columns},
                "modified": self.paths(pid).merged.stat().st_mtime}

    def merged_xlsx(self, pid: str) -> bytes:
        self.project(pid)
        if not self.paths(pid).merged.exists():
            raise NotFound("run the pipeline first")
        df = pd.read_csv(self.paths(pid).merged)
        buf = io.BytesIO()
        df.to_excel(buf, index=False, sheet_name="merged", engine="openpyxl")
        return buf.getvalue()

    # --------------------------------------------------------------- activity
    def activity(self, pid: str, limit: int = 500, before: Optional[int] = None) -> list[dict]:
        self.project(pid)
        return self.store.events(pid, limit=limit, before=before)

    def activity_csv(self, pid: str) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["time_utc", "kind", "message", "detail"])
        for e in reversed(self.activity(pid, limit=100_000)):
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(e["ts"])), e["kind"], e["message"],
                        json.dumps(e["detail"], sort_keys=True)])
        return buf.getvalue()

    # ----------------------------------------------------------------- bundle
    def bundle(self, pid: str) -> bytes:
        """A zip with everything needed to reproduce the results, plus a hash manifest."""
        p = self.project(pid)
        paths = self.paths(pid)
        buf = io.BytesIO()
        manifest: dict = {"project": p["name"], "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          "schemata_version": VERSION, "python": platform.python_version(),
                          "packages": _package_versions(), "files": []}
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(paths.root.rglob("*")):
                rel = f.relative_to(paths.root).as_posix()
                if not f.is_file() or rel.startswith(".") or "/." in rel or rel.endswith(".tmp"):
                    continue
                z.write(f, rel)
                manifest["files"].append({"path": rel, "size": f.stat().st_size, "sha256": file_hash(f)})
            z.writestr("activity.csv", self.activity_csv(pid))
            z.writestr("MANIFEST.json", json.dumps(manifest, indent=2))
            z.writestr("README.txt", _BUNDLE_README.format(name=p["name"], when=manifest["generated_at_utc"]))
        self.store.log(pid, "export", "Exported reproducibility bundle", n_files=len(manifest["files"]))
        return buf.getvalue()

    # ---------------------------------------------------------------- summary
    def summary(self, pid: str) -> dict:
        """Everything the project header needs, after picking up file changes."""
        p = self.project(pid)
        self.sync_files(pid)
        plan = self.plan(pid)
        files = []
        records = {f["name"]: f for f in self.store.files(pid)}
        for name, rec in records.items():
            df = plan["tables"].get(name)
            files.append({"name": name, "size": rec["size"], "sha256": rec["sha256"], "added_at": rec["added_at"],
                          "approved": rec["approved_sha256"] == rec["sha256"],
                          "changed_since_approval": rec["approved_sha256"] not in (None, rec["sha256"]),
                          "rows": None if df is None else len(df),
                          "columns": None if df is None else len(df.columns),
                          "read_as": plan["read_options"].get(name, {}).get("summary"),
                          "readable": name not in plan["read_errors"]})
        pipe = self.pipeline(pid)
        last = self.store.runs(pid, limit=1)
        return {**self._project_meta(p), "files": files, "raw_dir": str(self.paths(pid).raw),
                "counts": _count(plan["issues"]), "needs_approval": plan["needs_approval"],
                "unapproved": plan["unapproved"], "n_targets": len(self.targets(pid)),
                "merged_rows": plan["merged_rows"], "has_results": self.paths(pid).merged.exists(),
                "pipeline": {"running": pipe["running"], "current_step": pipe["current_step"],
                             "stale": pipe["stale"], "n_steps": len(pipe["graph"]["steps"]),
                             "error": pipe["error"]},
                "last_run": last[0] if last else None}

    # ---------------------------------------------------------------- watcher
    def auto_tick(self, pid: str, memo: dict) -> Optional[str]:
        """One watcher pass for an auto-run project. Returns what it did (for logs/tests)."""
        p = self.project(pid)
        if not p["auto_run"] or self.run_state(pid).running:
            return None
        self.sync_files(pid)
        plan = self.plan(pid)
        if not plan["tables"]:
            return None
        if plan["needs_approval"]:
            fresh = [i for i in plan["issues"] if i.file in plan["unapproved"] and i.severity in ("warning", "error")
                     and not _accepted_before(plan["plan"], i, plan["unapproved"])]
            blocking = [i for i in plan["issues"] if i.severity == "error"]
            if plan["config_changed"] or fresh or blocking or plan["read_errors"]:
                key = ("wait", tuple(sorted(plan["unapproved"])), len(fresh), len(blocking))
                if memo.get(pid) != key:
                    memo[pid] = key
                    if plan["unapproved"]:
                        self.store.log(pid, "auto", f"Auto-run is waiting for review: "
                                       f"{', '.join(plan['unapproved'])} need a look "
                                       f"({len(fresh) + len(blocking)} warning(s)/error(s))")
                return "waiting"
            self.approve(pid, trigger="auto")
            return "approved"
        stale = self.pipeline(pid)["stale"]
        if not stale:
            return None
        # Don't retry a failing step forever: only re-run when the inputs moved.
        fingerprint = json.dumps([[f["name"], f["sha256"]] for f in self.store.files(pid)] +
                                 [file_hash(self.paths(pid).clean_py) if self.paths(pid).clean_py.exists() else ""] +
                                 [self.steps(pid)], sort_keys=True)
        if memo.get(pid) == fingerprint:
            return None
        memo[pid] = fingerprint
        self.start_run(pid, trigger="auto")
        return "ran"


# ------------------------------------------------------------------- helpers
def _count(issues: list[Issue]) -> dict:
    out = {"error": 0, "warning": 0, "info": 0}
    for i in issues:
        out[i.severity] = out.get(i.severity, 0) + 1
    return out


def _accepted_before(plan, issue: Issue, unapproved: list[str]) -> bool:
    """An ambiguous-date warning on a new file is fine if an approved file made the same call."""
    if issue.kind != "date" or not issue.column or "could be" not in issue.message:
        return False
    by_file = {fp.file: fp for fp in plan.files}
    new = next((m for m in by_file[issue.file].mappings if m.source == issue.column), None)
    if new is None:
        return False
    return any(fp.mapping_for(new.target) and fp.mapping_for(new.target).date_format == new.date_format
               for name, fp in by_file.items() if name not in unapproved)


def _with_drift(plan, unapproved: list[str], approved: list[str]) -> list[Issue]:
    """Schema drift: a new file whose columns differ from the files already approved."""
    if not approved or not unapproved:
        return list(plan.issues)
    by_file = {fp.file: fp for fp in plan.files}
    everywhere = [t for t in plan.targets if all(by_file[f].mapping_for(t) for f in approved)]
    issues = []
    for i in plan.issues:
        if i.file in unapproved and i.kind == "mapping" and i.severity == "info":
            if i.message.startswith("column not mapped"):
                issues.append(Issue("warning", i.file, i.column,
                                    f"schema drift: '{i.column}' isn't in the schema and no approved file has it; "
                                    "add it to the schema or it will be dropped", "drift"))
                continue
            if i.message.startswith("no column found") and any(f"'{t}'" in i.message for t in everywhere):
                issues.append(Issue("warning", i.file, None,
                                    i.message.replace("no column found for", "schema drift: missing") +
                                    ", which every approved file has", "drift"))
                continue
        issues.append(i)
    return issues


def _override_changes(before: dict, after: dict) -> list[dict]:
    """Human-readable audit entries for mapping edits, with the previous value."""
    out = []
    for fname in sorted(set(before) | set(after)):
        b, a = before.get(fname, {}), after.get(fname, {})
        for col in sorted(set(b) | set(a)):
            ob, oa = b.get(col, {}), a.get(col, {})
            for key, label in (("target", "mapping"), ("unit", "unit"), ("date_format", "date format")):
                if ob.get(key, "<auto>") != oa.get(key, "<auto>"):
                    def show(v):
                        return "automatic" if v == "<auto>" else ("dropped" if v is None else str(v))
                    out.append({"message": f"{fname} · {col}: {label} {show(ob.get(key, '<auto>'))} → "
                                           f"{show(oa.get(key, '<auto>'))}",
                                "file": fname, "column": col, "field": key,
                                "previous": ob.get(key, "<auto>"), "value": oa.get(key, "<auto>")})
    return out


def _package_versions() -> dict:
    from importlib import metadata

    out = {}
    for name in ("pandas", "numpy", "pint", "openpyxl", "fastapi", "uvicorn"):
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    return out


_BUNDLE_README = """Schemata reproducibility bundle: {name}
Generated {when}

What's here
  raw/            the original files, untouched
  clean.py        the generated cleaning script (needs only pandas + openpyxl)
  schema.json     target columns, units and valid ranges
  overrides.json  the mapping decisions made during review
  pipeline.json   the analysis steps; steps/ holds their settings
  scripts/        any custom step scripts
  output/         merged.csv and every summary/figure the pipeline produced
  activity.csv    the full audit trail (who changed what, with previous values)
  MANIFEST.json   sha256 of every file, plus Python and package versions

Reproduce the merged table
  python clean.py raw output/merged.csv

Then compare output/merged.csv with the sha256 in MANIFEST.json.
"""
