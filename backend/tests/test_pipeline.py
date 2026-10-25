"""Run from backend/:  pytest -q"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import project_pipeline as pp  # noqa: E402
from pipeline.dag import Dag, DagError, Step  # noqa: E402
from pipeline.executor import Executor  # noqa: E402
from pipeline.hashing import file_hash  # noqa: E402



# ---------------------------------------------------------------------------
# Dag structure: edges are derived from declared inputs/outputs
# ---------------------------------------------------------------------------

def _chain_dag() -> Dag:
    return Dag([
        Step("a", ["{python}", "a.py"], inputs=["raw.txt"], outputs=["a.out"]),
        Step("b", ["{python}", "b.py"], inputs=["a.out"], outputs=["b.out"]),
        Step("c", ["{python}", "c.py"], inputs=["b.out"], outputs=["c.out"]),
    ])


def test_dag_topo_order_and_edges():
    dag = _chain_dag()
    dag.validate()
    assert dag.topo_order() == ["a", "b", "c"]
    assert dag.edges() == [("a", "b"), ("b", "c")]
    assert dag.parents("c") == ["b"]
    assert dag.children("a") == ["b"]


def test_dag_downstream_reachability():
    dag = _chain_dag()
    assert dag.downstream(["b"]) == ["b", "c"]
    assert dag.downstream(["a"]) == ["a", "b", "c"]
    assert dag.downstream(["c"], include_self=False) == []


def test_dag_detects_cycle():
    dag = Dag([
        Step("a", ["{python}", "a.py"], inputs=["c.out"], outputs=["a.out"]),
        Step("b", ["{python}", "b.py"], inputs=["a.out"], outputs=["b.out"]),
        Step("c", ["{python}", "c.py"], inputs=["b.out"], outputs=["c.out"]),
    ])
    with pytest.raises(DagError):
        dag.validate()


def test_dag_rejects_duplicate_output_writer():
    dag = Dag([
        Step("a", ["{python}", "a.py"], inputs=["raw.txt"], outputs=["shared.out"]),
        Step("b", ["{python}", "b.py"], inputs=["raw.txt"], outputs=["shared.out"]),
    ])
    with pytest.raises(DagError):
        dag.validate()


def test_dag_rejects_glob_output():
    dag = Dag([Step("a", ["{python}", "a.py"], inputs=["raw.txt"], outputs=["*.out"])])
    with pytest.raises(DagError):
        dag.validate()


# ---------------------------------------------------------------------------
# Hashing: deterministic, content-sensitive
# ---------------------------------------------------------------------------

def test_file_hash_deterministic_and_sensitive(tmp_path):
    p = tmp_path / "x.txt"
    p.write_bytes(b"hello")
    h1 = file_hash(p)
    assert h1 == file_hash(p)
    p.write_bytes(b"hello!")
    assert file_hash(p) != h1


# ---------------------------------------------------------------------------
# Executor on a tiny synthetic two-step pipeline (fast, fully isolated)
# ---------------------------------------------------------------------------

_STEP_A = """
from pathlib import Path
text = Path("raw.txt").read_text()
Path("a.out").write_text(text.strip().upper())
"""

_STEP_B = """
from pathlib import Path
text = Path("a.out").read_text()
Path("b.out").write_text(text + "!")
"""


@pytest.fixture
def chain_workdir(tmp_path):
    (tmp_path / "a.py").write_text(_STEP_A)
    (tmp_path / "b.py").write_text(_STEP_B)
    (tmp_path / "raw.txt").write_text("hello")
    dag = Dag([
        Step("a", ["{python}", "a.py"], inputs=["raw.txt", "a.py"], outputs=["a.out"]),
        Step("b", ["{python}", "b.py"], inputs=["a.out", "b.py"], outputs=["b.out"]),
    ])
    ex = Executor(dag, workdir=tmp_path, state_path=tmp_path / "state.json")
    return tmp_path, ex


def test_executor_runs_once_then_skips(chain_workdir):
    tmp_path, ex = chain_workdir
    r1 = ex.run()
    assert r1.ran == ["a", "b"]
    assert (tmp_path / "a.out").read_text() == "HELLO"
    assert (tmp_path / "b.out").read_text() == "HELLO!"

    r2 = ex.run()
    assert r2.ran == []
    assert r2.skipped == ["a", "b"]


def test_executor_cascades_real_content_change(chain_workdir):
    tmp_path, ex = chain_workdir
    ex.run()
    (tmp_path / "raw.txt").write_text("world")
    r = ex.run()
    assert r.ran == ["a", "b"]
    assert (tmp_path / "b.out").read_text() == "WORLD!"


def test_executor_stops_cascade_when_output_unchanged(chain_workdir):
    """Step 'a' normalizes (.strip().upper()). Adding whitespace changes the
    raw input's hash but not 'a's output, so 'b' must stay cached."""
    tmp_path, ex = chain_workdir
    ex.run()
    (tmp_path / "raw.txt").write_text("  hello  ")  # same after strip().upper()
    r = ex.run()
    assert r.ran == ["a"]
    assert r.skipped == ["b"]


def test_executor_force_reruns_everything(chain_workdir):
    tmp_path, ex = chain_workdir
    ex.run()
    r = ex.run(force=True)
    assert r.mode == "full"
    assert r.ran == ["a", "b"]


def test_status_reports_fresh_and_stale(chain_workdir):
    tmp_path, ex = chain_workdir
    assert all(not s.fresh for s in ex.status())  # never run -> stale

    ex.run()
    assert all(s.fresh for s in ex.status())

    (tmp_path / "raw.txt").write_text("world")
    statuses = {s.step: s for s in ex.status()}
    assert not statuses["a"].fresh
    assert not statuses["b"].fresh  # transitively stale, even before re-running
    assert "upstream" in statuses["b"].reason


def test_benchmark_incremental_is_cheap(chain_workdir):
    _, ex = chain_workdir
    bench = ex.benchmark()
    assert bench["full"]["ran"] == ["a", "b"]
    assert bench["incremental"]["ran"] == []
    assert bench["incremental"]["total_duration_s"] < bench["full"]["total_duration_s"]


def test_executor_stops_on_failure(tmp_path):
    (tmp_path / "raw.txt").write_text("hello")
    (tmp_path / "a.py").write_text("import sys\nsys.exit(1)\n")
    (tmp_path / "b.py").write_text('from pathlib import Path\nPath("b.out").write_text("ran")\n')
    dag = Dag([
        Step("a", ["{python}", "a.py"], inputs=["raw.txt"], outputs=["a.out"]),
        Step("b", ["{python}", "b.py"], inputs=["a.out"], outputs=["b.out"]),
    ])
    ex = Executor(dag, workdir=tmp_path, state_path=tmp_path / "state.json")
    r = ex.run()
    assert r.failed == ["a"]
    assert r.ran == ["a"]  # b is never attempted after a fails
    assert not (tmp_path / "b.out").exists()

    statuses = {s.step: s for s in ex.status()}
    assert not statuses["a"].fresh and "failed" in statuses["a"].reason


# ---------------------------------------------------------------------------
# Project pipelines: clean -> configured steps, built from pipeline.json
# ---------------------------------------------------------------------------

TARGETS = {
    "timestamp": {"kind": "datetime"},
    "temperature": {"kind": "quantity", "unit": "degC"},
    "ph": {"kind": "number"},
    "notes": {"kind": "text"},
}


def test_project_dag_shape(tmp_path):
    steps = pp.validate_steps(pp.default_steps(TARGETS, sample=True), TARGETS, tmp_path)
    dag = pp.build_dag(tmp_path, steps, TARGETS)
    assert dag.topo_order() == ["clean", "summary", "temp_by_file", "temperature_over_time"]
    assert set(dag.children("clean")) == {"summary", "temp_by_file", "temperature_over_time"}
    params = (tmp_path / "steps" / "temperature_over_time.json").read_text()
    assert '"x_is_time": true' in params and '"_code"' in params


def test_step_validation_rejects_bad_steps(tmp_path):
    bad = [
        [{"name": "clean", "kind": "summary"}],
        [{"name": "a", "kind": "chart", "params": {"chart": "bar", "y": "notes"}}],
        [{"name": "a", "kind": "chart", "params": {"chart": "pie", "y": "ph"}}],
        [{"name": "a", "kind": "script", "params": {"script": "missing.py", "outputs": ["output/a.csv"]}}],
        [{"name": "a", "kind": "summary"}, {"name": "a", "kind": "summary"}],
    ]
    for steps in bad:
        with pytest.raises(pp.StepError):
            pp.validate_steps(steps, TARGETS, tmp_path)

    (tmp_path / "fit.py").write_text("print('hi')")
    for outputs in (["../escape.csv"], ["output/merged.csv"], ["/etc/x"], ["elsewhere.csv"]):
        with pytest.raises(pp.StepError):
            pp.validate_steps([{"name": "fit", "kind": "script",
                                "params": {"script": "fit.py", "outputs": outputs}}], TARGETS, tmp_path)


def test_removed_step_params_are_cleaned_up(tmp_path):
    steps = pp.validate_steps([{"name": "s1", "kind": "summary"}, {"name": "s2", "kind": "summary"}],
                              TARGETS, tmp_path)
    pp.build_dag(tmp_path, steps, TARGETS)
    pp.build_dag(tmp_path, steps[:1], TARGETS)
    assert sorted(f.name for f in (tmp_path / "steps").iterdir()) == ["s1.json"]
