"""Executor: re-run only the steps downstream of a change, skip the rest.

Steps run in topological order. Each step's staleness is judged against its
*current* input files -- and since upstream steps have already (possibly) run
by the time we get to a downstream step, a change only cascades if it actually
changed the bytes of an intermediate file. If step A reruns but produces an
identical output, step B is skipped: real caching, not just "did A run".

A status tracker (`status()`) runs the same staleness check with no side
effects, for the dashboard. There it also reports "stale" transitively through
the DAG when an upstream step hasn't been re-run yet, since that's what a user
should see as "not trustworthy yet" before actually running the pipeline.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .dag import Dag
from .hashing import (
    PipelineState,
    StepRecord,
    current_input_hashes,
    current_output_hashes,
    is_directly_stale,
)

_TAIL = 2000  # chars of stdout/stderr to keep per step


@dataclass
class RunResult:
    step: str
    ran: bool          # True = executed; False = skipped because it was fresh
    reason: str
    duration_s: float = 0.0
    returncode: Optional[int] = None
    stdout: str = ""
    stderr: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExecutionReport:
    mode: str  # "full" | "incremental"
    results: list[RunResult] = field(default_factory=list)
    total_duration_s: float = 0.0

    @property
    def ran(self) -> list[str]:
        return [r.step for r in self.results if r.ran]

    @property
    def skipped(self) -> list[str]:
        return [r.step for r in self.results if not r.ran]

    @property
    def failed(self) -> list[str]:
        return [r.step for r in self.results if r.ran and r.returncode not in (0, None)]

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "total_duration_s": self.total_duration_s,
            "ran": self.ran,
            "skipped": self.skipped,
            "failed": self.failed,
            "results": [r.to_dict() for r in self.results],
        }


@dataclass
class StepStatus:
    step: str
    fresh: bool
    reason: str
    outputs_exist: bool
    last_ran_at: Optional[float]
    last_duration_s: float

    def to_dict(self) -> dict:
        return asdict(self)


class Executor:
    """Runs a Dag against a workdir, persisting state at state_path."""

    def __init__(self, dag: Dag, workdir: Path, state_path: Path):
        dag.validate()
        self.dag = dag
        self.workdir = Path(workdir)
        self.state_path = Path(state_path)

    def _load(self) -> PipelineState:
        return PipelineState.load(self.state_path)

    def run(self, force: bool = False, only: Optional[list[str]] = None) -> ExecutionReport:
        """Run the pipeline. `force` reruns every step (a "full" run, for timing
        comparisons). `only` limits which steps are even considered -- their
        upstream dependencies still run first if they're stale."""
        state = self._load()
        order = self.dag.topo_order()
        if only is not None:
            order = [n for n in order if n in set(self.dag.downstream(only))]

        results: list[RunResult] = []
        t_start = time.time()
        for name in order:
            step = self.dag[name]
            record = state.get(name)
            stale, reason = (True, "forced full run") if force else is_directly_stale(step, self.workdir, record)

            if not stale:
                results.append(RunResult(name, ran=False, reason=reason))
                continue

            t0 = time.time()
            proc = subprocess.run(step.argv(), cwd=self.workdir, capture_output=True, text=True)
            duration = time.time() - t0

            new_record = StepRecord(
                status="ok" if proc.returncode == 0 else "failed",
                input_hashes=current_input_hashes(step, self.workdir),
                output_hashes=current_output_hashes(step, self.workdir) if proc.returncode == 0 else {},
                duration_s=duration,
                ran_at=time.time(),
                returncode=proc.returncode,
                stdout_tail=proc.stdout[-_TAIL:],
                stderr_tail=proc.stderr[-_TAIL:],
            )
            state.set(name, new_record)
            results.append(RunResult(name, ran=True, reason=reason, duration_s=duration,
                                      returncode=proc.returncode, stdout=proc.stdout[-_TAIL:],
                                      stderr=proc.stderr[-_TAIL:]))
            if proc.returncode != 0:
                break  # don't run downstream steps on top of a failure

        state.save(self.state_path)
        return ExecutionReport(mode="full" if force else "incremental", results=results,
                                total_duration_s=time.time() - t_start)

    def status(self) -> list[StepStatus]:
        """Dry-run staleness check for every step, no side effects."""
        state = self._load()
        direct: dict[str, tuple[bool, str]] = {}
        out: list[StepStatus] = []
        for name in self.dag.topo_order():
            step = self.dag[name]
            record = state.get(name)
            stale, reason = is_directly_stale(step, self.workdir, record)
            direct[name] = (stale, reason)

            upstream_stale = [p for p in self.dag.parents(name) if direct[p][0]]
            if not stale and upstream_stale:
                stale = True
                reason = f"upstream step(s) stale: {', '.join(upstream_stale)}"

            out.append(StepStatus(
                step=name,
                fresh=not stale,
                reason=reason,
                outputs_exist=all((self.workdir / o).is_file() for o in step.outputs),
                last_ran_at=record.ran_at,
                last_duration_s=record.duration_s,
            ))
        return out

    def benchmark(self) -> dict:
        """Force a full run, then an incremental no-op run, and report both
        timings -- the "time saved" number for the demo."""
        full = self.run(force=True)
        incremental = self.run(force=False)
        speedup = (full.total_duration_s / incremental.total_duration_s
                   if incremental.total_duration_s > 0 else None)
        return {
            "full": full.to_dict(),
            "incremental": incremental.to_dict(),
            "speedup_x": round(speedup, 1) if speedup else None,
        }
