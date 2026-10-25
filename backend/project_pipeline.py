"""A project's pipeline: clean -> the analysis steps the user configured.

Step one is always "clean": the generated clean.py over raw/. After that come
steps from pipeline.json, each one of:

  summary  per-file summary statistics            -> output/<name>.csv
  chart    bar / line / scatter SVG + its data    -> output/figures/<name>.svg (+ .csv)
  script   your own Python script in scripts/     -> the outputs you declare

Built-in steps get their parameters from steps/<name>.json, which is a
declared input -- so changing a chart's settings re-runs exactly that chart.
The parameters also carry a hash of the step's code, so upgrading Schemata
re-runs steps whose code changed.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

from merger import units
from pipeline.dag import Dag, DagError, Step, norm

STEPS_CODE = Path(__file__).resolve().parent / "pipeline_steps"
RAW_GLOBS = ["raw/*.csv", "raw/*.tsv", "raw/*.txt", "raw/*.xlsx", "raw/*.xlsm"]
KINDS = ("summary", "chart", "script")
CHARTS = ("bar", "line", "scatter")
AGGS = ("median", "mean", "sum", "count", "min", "max")
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_RESERVED_OUTPUTS = {"output/merged.csv", "output/pipeline_state.json"}


class StepError(ValueError):
    pass


def _code_hash(*files: str) -> str:
    h = hashlib.sha256()
    for f in files:
        h.update((STEPS_CODE / f).read_bytes())
    return h.hexdigest()[:16]


def _safe_rel(path: str, what: str) -> str:
    p = norm(str(path).strip())
    if not p or p.startswith("/") or ".." in Path(p).parts or re.match(r"^[A-Za-z]:", p):
        raise StepError(f"{what} '{path}' must be a path inside the project folder")
    return p


def default_steps(targets: dict, sample: bool = False) -> list[dict]:
    steps: list[dict] = [{"name": "summary", "kind": "summary", "title": "Summary statistics", "params": {}}]
    if sample:
        steps += [
            {"name": "temp_by_file", "kind": "chart", "title": "Median temperature by file",
             "params": {"chart": "bar", "y": "temperature", "x": "source_file", "agg": "median"}},
            {"name": "temperature_over_time", "kind": "chart", "title": "Temperature over time",
             "params": {"chart": "line", "x": "timestamp", "y": "temperature", "group_by": "source_file"}},
        ]
    return steps


def validate_steps(steps: list, targets: dict, scripts_dir: Path) -> list[dict]:
    """Check user-supplied steps and return normalized copies. Raises StepError."""
    if not isinstance(steps, list):
        raise StepError("steps must be a list")
    columns = {"source_file", *targets}
    out, names = [], set()
    for raw in steps:
        if not isinstance(raw, dict):
            raise StepError("each step must be an object")
        name = str(raw.get("name", "")).strip()
        if not _NAME.match(name) or name == "clean":
            raise StepError(f"step name '{name}' must be 1-40 lowercase letters, digits, - or _ (and not 'clean')")
        if name in names:
            raise StepError(f"two steps are called '{name}'")
        names.add(name)
        kind = raw.get("kind")
        if kind not in KINDS:
            raise StepError(f"step '{name}': kind must be one of {', '.join(KINDS)}")
        params = dict(raw.get("params") or {})
        clean: dict = {"name": name, "kind": kind, "title": str(raw.get("title") or "")[:120], "params": {}}

        if kind == "summary":
            cols = [c for c in params.get("columns") or [] if c in columns]
            clean["params"] = {"columns": cols, "group_by": params.get("group_by", "source_file")
                               if params.get("group_by", "source_file") in columns | {""} else "source_file"}
        elif kind == "chart":
            chart = params.get("chart", "bar")
            if chart not in CHARTS:
                raise StepError(f"step '{name}': chart must be one of {', '.join(CHARTS)}")
            y = params.get("y")
            if y not in targets or targets[y]["kind"] not in ("quantity", "number"):
                raise StepError(f"step '{name}': y must be a numeric column of the schema")
            p = {"chart": chart, "y": y}
            if chart == "bar":
                x = params.get("x") or "source_file"
                if x not in columns:
                    raise StepError(f"step '{name}': unknown category column '{x}'")
                agg = params.get("agg", "median")
                if agg not in AGGS:
                    raise StepError(f"step '{name}': aggregation must be one of {', '.join(AGGS)}")
                p.update(x=x, agg=agg)
            else:
                x = params.get("x")
                if x not in targets or targets[x]["kind"] not in ("quantity", "number", "datetime"):
                    raise StepError(f"step '{name}': x must be a numeric or date column of the schema")
                g = params.get("group_by", "source_file")
                p.update(x=x, group_by=g if g in columns | {""} else "source_file")
            if params.get("title"):
                p["title"] = str(params["title"])[:120]
            clean["params"] = p
        else:
            script = str(params.get("script", "")).strip()
            if not re.match(r"^[A-Za-z0-9_.-]+\.py$", script):
                raise StepError(f"step '{name}': script must be a .py file name in scripts/")
            if not (scripts_dir / script).is_file():
                raise StepError(f"step '{name}': scripts/{script} doesn't exist yet; upload it first")
            inputs = [_safe_rel(i, "input") for i in params.get("inputs") or ["output/merged.csv"]]
            outputs = [_safe_rel(o, "output") for o in params.get("outputs") or []]
            if not outputs:
                raise StepError(f"step '{name}': declare at least one output file (e.g. output/{name}.csv)")
            for o in outputs:
                if not o.startswith("output/") or o in _RESERVED_OUTPUTS:
                    raise StepError(f"step '{name}': outputs must be new files under output/ (not '{o}')")
            clean["params"] = {"script": script, "inputs": inputs, "outputs": outputs}
        out.append(clean)
    return out


def outputs_of(step: dict) -> list[str]:
    name, kind = step["name"], step["kind"]
    if kind == "summary":
        return [f"output/{name}.csv"]
    if kind == "chart":
        return [f"output/figures/{name}.svg", f"output/figures/{name}.csv"]
    return list(step["params"]["outputs"])


def _write_if_changed(path: Path, text: str) -> None:
    if not path.exists() or path.read_text() != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def build_dag(root: Path, steps: list[dict], targets: dict) -> Dag:
    """Build the project's DAG, writing steps/<name>.json for built-in steps."""
    dag_steps = [Step(
        name="clean", title="Clean & merge",
        description="Runs the generated clean.py over raw/ to produce output/merged.csv.",
        command=["{python}", "clean.py", "raw", "output/merged.csv"],
        inputs=[*RAW_GLOBS, "clean.py"], outputs=["output/merged.csv"],
    )]
    steps_dir = root / "steps"
    wanted = set()
    for s in steps:
        name, kind, p = s["name"], s["kind"], dict(s["params"])
        if kind in ("summary", "chart"):
            params_file = f"steps/{name}.json"
            wanted.add(f"{name}.json")
            if kind == "chart":
                y_spec = targets.get(p["y"], {})
                p.setdefault("y_label", p["y"] + (f" ({units.pretty(y_spec['unit'])})" if y_spec.get("unit") else ""))
                x_spec = targets.get(p.get("x", ""), {})
                p["x_is_time"] = x_spec.get("kind") == "datetime"
                if x_spec.get("unit"):
                    p.setdefault("x_label", f"{p['x']} ({units.pretty(x_spec['unit'])})")
                if s.get("title") and not p.get("title"):
                    p["title"] = s["title"]
                p["_code"] = _code_hash("chart.py", "svgchart.py")
                command = ["{python}", str(STEPS_CODE / "chart.py"), params_file, "output/merged.csv",
                           *outputs_of(s)]
            else:
                p["_code"] = _code_hash("summary.py")
                command = ["{python}", str(STEPS_CODE / "summary.py"), params_file, "output/merged.csv",
                           *outputs_of(s)]
            _write_if_changed(root / params_file, json.dumps(p, indent=2, sort_keys=True))
            inputs = ["output/merged.csv", params_file]
            description = (f"{p['chart'].capitalize()} chart of {p['y']}" if kind == "chart"
                           else "Summary statistics per file")
        else:
            command = ["{python}", f"scripts/{p['script']}", *p["inputs"], *p["outputs"]]
            inputs = [*p["inputs"], f"scripts/{p['script']}"]
            description = f"Runs scripts/{p['script']}"
        dag_steps.append(Step(name=name, title=s.get("title") or name.replace("_", " "),
                              description=description, command=command, inputs=inputs,
                              outputs=outputs_of(s)))
    # Parameter files of deleted steps would otherwise linger forever.
    if steps_dir.is_dir():
        for f in steps_dir.glob("*.json"):
            if f.name not in wanted:
                f.unlink()
    dag = Dag(dag_steps)
    try:
        dag.validate()
    except DagError as e:
        raise StepError(str(e))
    return dag


def step_kinds() -> dict:
    return {"kinds": list(KINDS), "charts": list(CHARTS), "aggs": list(AGGS)}


def find_step(steps: list[dict], name: str) -> Optional[dict]:
    return next((s for s in steps if s["name"] == name), None)
