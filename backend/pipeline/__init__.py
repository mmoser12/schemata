"""Pipeline runner: DAG registry, change detection, incremental executor, status.

  - dag.py       Dag / Step: steps declare inputs & outputs; edges are derived.
  - hashing.py    file hashing + PipelineState: what changed since the last run.
  - executor.py   Executor: runs stale steps, skips fresh ones, tracks timing.
  - demo.py       the concrete clean -> analyze -> figures demo pipeline.

The generated clean.py from the merger becomes the first step automatically
(see demo.ensure_clean_script).
"""

from .dag import Dag, DagError, Step
from .executor import ExecutionReport, Executor, RunResult, StepStatus
from .hashing import PipelineState, StepRecord

__all__ = [
    "Dag", "DagError", "Step",
    "Executor", "ExecutionReport", "RunResult", "StepStatus",
    "PipelineState", "StepRecord",
]
