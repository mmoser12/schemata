"""Pipeline runner: DAG registry, change detection, incremental executor, status.

  - dag.py       Dag / Step: steps declare inputs & outputs; edges are derived.
  - hashing.py    file hashing + PipelineState: what changed since the last run.
  - executor.py   Executor: runs stale steps, skips fresh ones, tracks timing.

Projects build their concrete DAG (clean -> summary/charts/scripts) in
backend/project_pipeline.py; the generated clean.py is always step one.
"""

from .dag import Dag, DagError, Step
from .executor import ExecutionReport, Executor, RunResult, StepStatus
from .hashing import PipelineState, StepRecord

__all__ = [
    "Dag", "DagError", "Step",
    "Executor", "ExecutionReport", "RunResult", "StepStatus",
    "PipelineState", "StepRecord",
]
