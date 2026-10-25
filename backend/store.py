"""Persistence: one SQLite file (stdlib sqlite3) plus a folder per project.

    <data dir>/
      schemata.db             projects, files, activity log, run history
      projects/<id>/
        raw/                  original files, never modified (also the watch folder)
        schema.json           target columns
        overrides.json        reviewed mapping decisions
        pipeline.json         analysis steps after "clean"
        clean.py              generated cleaning script (step one of the pipeline)
        steps/<name>.json     parameters of built-in steps (hashed like any input)
        scripts/              your own step scripts
        output/               merged.csv, summaries, figures, pipeline_state.json

The database only holds metadata and history; everything needed to reproduce
a result lives in the project folder as plain files.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    auto_run INTEGER NOT NULL DEFAULT 0,
    approved_config TEXT
);
CREATE TABLE IF NOT EXISTS files (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    added_at REAL NOT NULL,
    approved_sha256 TEXT,
    PRIMARY KEY (project_id, name)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_by_project ON events(project_id, id);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    ts REAL NOT NULL,
    trigger TEXT NOT NULL,
    mode TEXT NOT NULL,
    duration_s REAL NOT NULL,
    time_saved_s REAL NOT NULL,
    ran TEXT NOT NULL,
    skipped TEXT NOT NULL,
    failed TEXT NOT NULL,
    results TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_by_project ON runs(project_id, id);
"""


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def _one(self, sql: str, args: tuple = ()) -> Optional[dict]:
        rows = self._all(sql, args)
        return rows[0] if rows else None

    def _exec(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            return self._db.execute(sql, args).lastrowid

    # -- projects ----------------------------------------------------------
    def create_project(self, pid: str, name: str, description: str = "") -> None:
        now = time.time()
        self._exec("INSERT INTO projects (id, name, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                   (pid, name, description, now, now))

    def projects(self) -> list[dict]:
        return self._all("SELECT * FROM projects ORDER BY updated_at DESC")

    def project(self, pid: str) -> Optional[dict]:
        return self._one("SELECT * FROM projects WHERE id = ?", (pid,))

    def update_project(self, pid: str, **fields: Any) -> None:
        allowed = {"name", "description", "auto_run", "approved_config"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        sets["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in sets)
        self._exec(f"UPDATE projects SET {cols} WHERE id = ?", (*sets.values(), pid))

    def touch(self, pid: str) -> None:
        self._exec("UPDATE projects SET updated_at = ? WHERE id = ?", (time.time(), pid))

    def delete_project(self, pid: str) -> None:
        self._exec("DELETE FROM projects WHERE id = ?", (pid,))

    # -- files -------------------------------------------------------------
    def files(self, pid: str) -> list[dict]:
        return self._all("SELECT * FROM files WHERE project_id = ? ORDER BY added_at, name", (pid,))

    def upsert_file(self, pid: str, name: str, sha256: str, size: int, mtime_ns: int) -> None:
        self._exec(
            "INSERT INTO files (project_id, name, sha256, size, mtime_ns, added_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id, name) DO UPDATE SET sha256 = excluded.sha256, size = excluded.size, "
            "mtime_ns = excluded.mtime_ns",
            (pid, name, sha256, size, mtime_ns, time.time()))

    def delete_file(self, pid: str, name: str) -> None:
        self._exec("DELETE FROM files WHERE project_id = ? AND name = ?", (pid, name))

    def approve_files(self, pid: str) -> None:
        self._exec("UPDATE files SET approved_sha256 = sha256 WHERE project_id = ?", (pid,))

    # -- activity log ------------------------------------------------------
    def log(self, pid: str, kind: str, message: str, **detail: Any) -> None:
        self._exec("INSERT INTO events (project_id, ts, kind, message, detail) VALUES (?, ?, ?, ?, ?)",
                   (pid, time.time(), kind, message, json.dumps(detail, default=str)))
        self.touch(pid)

    def events(self, pid: str, limit: int = 500, before: Optional[int] = None) -> list[dict]:
        if before:
            rows = self._all("SELECT * FROM events WHERE project_id = ? AND id < ? ORDER BY id DESC LIMIT ?",
                             (pid, before, limit))
        else:
            rows = self._all("SELECT * FROM events WHERE project_id = ? ORDER BY id DESC LIMIT ?", (pid, limit))
        for r in rows:
            r["detail"] = json.loads(r["detail"] or "{}")
        return rows

    # -- run history -------------------------------------------------------
    def add_run(self, pid: str, trigger: str, report: dict, time_saved_s: float) -> int:
        return self._exec(
            "INSERT INTO runs (project_id, ts, trigger, mode, duration_s, time_saved_s, ran, skipped, failed, results) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, time.time(), trigger, report["mode"], report["total_duration_s"], time_saved_s,
             json.dumps(report["ran"]), json.dumps(report["skipped"]), json.dumps(report["failed"]),
             json.dumps(report["results"])))

    def runs(self, pid: str, limit: int = 50) -> list[dict]:
        rows = self._all("SELECT * FROM runs WHERE project_id = ? ORDER BY id DESC LIMIT ?", (pid, limit))
        for r in rows:
            for k in ("ran", "skipped", "failed", "results"):
                r[k] = json.loads(r[k])
        return rows
