"""Run from backend/:  pytest -q"""

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from merger import apply_plan, build_plan, generate_clean_script, read_table  # noqa: E402
from merger.headers import match_header, split_header  # noqa: E402
from merger.schema import TARGETS  # noqa: E402
from merger.units import linear_coefficients  # noqa: E402

SAMPLE = Path(__file__).resolve().parents[2] / "sample_data"


@pytest.fixture(scope="module")
def tables():
    if not any(SAMPLE.glob("*.xlsx")):
        subprocess.run([sys.executable, str(SAMPLE / "make_sample_data.py")], check=True)
    return {p.name: read_table(p) for p in sorted(SAMPLE.iterdir()) if p.suffix in {".csv", ".xlsx"}}


@pytest.mark.parametrize("raw,name,unit", [
    ("Temp (C)", "temp", "c"),
    ("Mass [mg]", "mass", "mg"),
    ("temperature_K", "temperature", "k"),
    ("P_kPa", "p", "kpa"),
    ("sample_id", "sample id", None),
    ("SampleID", "sample id", None),
    ("Date", "date", None),
])
def test_split_header(raw, name, unit):
    assert split_header(raw) == (name, unit)


def test_fuzzy_match():
    m = match_header("temprature", TARGETS)
    assert m.target == "temperature" and m.method == "fuzzy"


def test_unit_coefficients():
    a, b = linear_coefficients("degF", "degC")
    assert abs(a * 212 + b - 100) < 1e-9
    assert linear_coefficients("milligram", "g")[0] == pytest.approx(0.001)


def test_secret_fahrenheit_is_flagged(tables):
    plan = build_plan(tables)
    carol = next(fp for fp in plan.files if fp.file.startswith("sensor_carol"))
    temp = carol.mapping_for("temperature")
    assert temp.unit == "degF" and temp.unit_source == "inferred"
    assert any(i.kind == "unit_conflict" and i.file == carol.file and i.severity == "warning"
               for i in plan.issues)


def test_merged_values_agree(tables):
    merged, validation = apply_plan(tables, build_plan(tables))
    assert len(merged) == 36
    assert not [i for i in validation if i.severity == "error"]
    assert merged["temperature"].between(19.5, 26.5).all()
    assert merged["mass"].between(1.0, 4.0).all()
    assert merged["pressure"].between(98, 104).all()
    assert merged["timestamp"].notna().all()


def test_generated_script_reproduces_merge(tables, tmp_path):
    plan = build_plan(tables)
    merged, _ = apply_plan(tables, plan)
    for p in SAMPLE.iterdir():
        if p.suffix in {".csv", ".xlsx"}:
            (tmp_path / p.name).write_bytes(p.read_bytes())
    script = tmp_path / "clean.py"
    script.write_text(generate_clean_script(plan))
    subprocess.run([sys.executable, str(script), str(tmp_path), str(tmp_path / "out.csv")], check=True)
    expected = merged.copy()
    expected.to_csv(tmp_path / "expected.csv", index=False)
    pd.testing.assert_frame_equal(pd.read_csv(tmp_path / "out.csv"), pd.read_csv(tmp_path / "expected.csv"))


def test_api_roundtrip():
    from main import app

    client = TestClient(app)
    files = [("files", (p.name, p.read_bytes())) for p in sorted(SAMPLE.iterdir()) if p.suffix in {".csv", ".xlsx"}]
    r = client.post("/merge/preview", files=files)
    assert r.status_code == 200
    body = r.json()
    sid = body["session_id"]

    # The review UI sends the (possibly edited) plan back.
    plan = body["plan"]
    r = client.post(f"/merge/{sid}/apply", json=plan)
    assert r.status_code == 200 and r.json()["n_rows"] == 36

    assert client.get(f"/merge/{sid}/merged.csv").text.startswith("source_file,")
    assert "def main()" in client.get(f"/merge/{sid}/clean.py").text
