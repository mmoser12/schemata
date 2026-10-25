"""Change detector: hash declared files and persist the hashes between runs.

A step is stale when any of its *current* input hashes differ from the hashes
recorded the last time it ran successfully, or when one of its declared
outputs is missing. Everything here is pure file I/O + sha256 -- no process
execution, so it doubles as the read-only "what's stale" status check.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .dag import Step, is_glob, norm

_CHUNK = 1 << 20  # 1 MiB


# (path, mtime_ns, size) -> sha256. Status polls hash every input; unchanged
# files are answered from here instead of being re-read. Like git, we only
# trust the cache for files whose mtime is safely in the past: a same-size
# rewrite within the filesystem's timestamp granularity would otherwise look
# unchanged.
_RACY_NS = 2_000_000_000
_HASH_CACHE: dict[tuple[str, int, int], str] = {}


def file_hash(path: Path) -> str:
    """sha256 of a file's bytes, streamed so large files don't blow up memory."""
    st = Path(path).stat()
    key = (str(Path(path).resolve()), st.st_mtime_ns, st.st_size)
    cached = _HASH_CACHE.get(key)
    if cached is not None:
        return cached
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    digest = h.hexdigest()
    if time.time_ns() - st.st_mtime_ns > _RACY_NS:
        if len(_HASH_CACHE) > 10_000:
            _HASH_CACHE.clear()
        _HASH_CACHE[key] = digest
    return digest


def resolve_paths(pattern: str, workdir: Path) -> list[Path]:
    """Expand one declared input/output pattern against the workspace."""
    if is_glob(pattern):
        return sorted(workdir.glob(pattern))
    p = workdir / pattern
    return [p] if p.exists() else []


def hash_paths(paths: list[Path], workdir: Path) -> dict[str, str]:
    """{workdir-relative posix path: sha256} for files that exist."""
    return {norm(str(p.relative_to(workdir))): file_hash(p) for p in paths if p.is_file()}


def current_input_hashes(step: Step, workdir: Path) -> dict[str, str]:
    paths: list[Path] = []
    for pattern in step.inputs:
        paths.extend(resolve_paths(pattern, workdir))
    return hash_paths(paths, workdir)


def current_output_hashes(step: Step, workdir: Path) -> dict[str, str]:
    paths = [workdir / o for o in step.outputs]
    return hash_paths(paths, workdir)


def missing_outputs(step: Step, workdir: Path) -> list[str]:
    return [o for o in step.outputs if not (workdir / o).is_file()]


@dataclass
class StepRecord:
    """What we know about a step's last successful (or last attempted) run."""

    status: str = "never_run"          # never_run | ok | failed
    input_hashes: dict[str, str] = field(default_factory=dict)
    output_hashes: dict[str, str] = field(default_factory=dict)
    duration_s: float = 0.0
    ran_at: Optional[float] = None     # unix timestamp
    returncode: Optional[int] = None
    stdout_tail: str = ""
    stderr_tail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StepRecord":
        return cls(
            status=d.get("status", "never_run"),
            input_hashes=dict(d.get("input_hashes") or {}),
            output_hashes=dict(d.get("output_hashes") or {}),
            duration_s=d.get("duration_s", 0.0),
            ran_at=d.get("ran_at"),
            returncode=d.get("returncode"),
            stdout_tail=d.get("stdout_tail", ""),
            stderr_tail=d.get("stderr_tail", ""),
        )


@dataclass
class PipelineState:
    """Per-step records, persisted as JSON next to the pipeline's workdir."""

    steps: dict[str, StepRecord] = field(default_factory=dict)

    def get(self, name: str) -> StepRecord:
        return self.steps.get(name, StepRecord())

    def set(self, name: str, record: StepRecord) -> None:
        self.steps[name] = record

    def to_dict(self) -> dict:
        return {"steps": {name: r.to_dict() for name, r in self.steps.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "PipelineState":
        return cls(steps={name: StepRecord.from_dict(r) for name, r in d.get("steps", {}).items()})

    @classmethod
    def load(cls, path: Path) -> "PipelineState":
        if not path.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(path.read_text()))
        except (json.JSONDecodeError, KeyError, TypeError):
            return cls()  # corrupt/empty state file -> treat everything as never_run

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))


def is_directly_stale(step: Step, workdir: Path, record: StepRecord) -> tuple[bool, str]:
    """Is this step stale judged only by its own declared files (no upstream awareness)?"""
    if record.status != "ok":
        return True, "never run" if record.status == "never_run" else "last run failed"

    missing = missing_outputs(step, workdir)
    if missing:
        return True, f"missing output(s): {', '.join(missing)}"

    current = current_input_hashes(step, workdir)
    if current != record.input_hashes:
        added = set(current) - set(record.input_hashes)
        removed = set(record.input_hashes) - set(current)
        changed = {k for k in current.keys() & record.input_hashes.keys() if current[k] != record.input_hashes[k]}
        parts = []
        if changed:
            parts.append(f"changed: {', '.join(sorted(changed))}")
        if added:
            parts.append(f"added: {', '.join(sorted(added))}")
        if removed:
            parts.append(f"removed: {', '.join(sorted(removed))}")
        return True, "input(s) " + "; ".join(parts)

    out_now = current_output_hashes(step, workdir)
    if out_now != record.output_hashes:
        return True, "output file(s) modified outside the pipeline"

    return False, "inputs and outputs unchanged since last successful run"


now = time.time  # small seam for tests that want to freeze/inspect timing
