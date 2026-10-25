"""Run from backend/:  pytest -q"""

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from merger import (apply_plan, build_plan, column_previews, generate_clean_script, load, normalize_targets,
                    read_table, same_script, suggest_targets)
from merger.headers import match_header, normalize_name, split_header
from merger.schema import TARGETS, SchemaError
from merger.units import linear_coefficients, parse_unit, pretty, token_to_unit

SAMPLE = Path(__file__).resolve().parents[2] / "sample_data"


@pytest.fixture(scope="module")
def tables():
    if not any(SAMPLE.glob("*.xlsx")):
        subprocess.run([sys.executable, str(SAMPLE / "make_sample_data.py")], check=True)
    return {p.name: read_table(p) for p in sorted(SAMPLE.iterdir()) if p.suffix in {".csv", ".xlsx"}}


@pytest.fixture
def messy(tmp_path):
    """Exports the way instruments and European spreadsheets actually write them."""
    eu = ("Instrument: Thermo X-200\nOperator;Dana\n\n"
          "Sample;Datum;Temp (°C);Conc (mM)\nS1;01.09.2026;21,5;0,25\nS2;02.09.2026;22,1;0,30\n")
    (tmp_path / "eu_export.csv").write_bytes(eu.encode("cp1252"))
    with pd.ExcelWriter(tmp_path / "report.xlsx") as w:
        pd.DataFrame({"note": []}).to_excel(w, sheet_name="Notes", index=False)
        pd.DataFrame([["Run report", None, None], [None, None, None], ["sample_id", "mass_mg", "pH"],
                      ["X1", 1200, 7.1], ["X2", 1300, 6.9]]).to_excel(w, sheet_name="Data", index=False,
                                                                       header=False)
    (tmp_path / "plain.tsv").write_text("sample\ttemp_F\n" + "T1\t70.0\nT2\t72.5\n")
    return tmp_path


@pytest.mark.parametrize("raw,name,unit", [
    ("Temp (C)", "temp", "C"),
    ("Mass [mg]", "mass", "mg"),
    ("temperature_K", "temperature", "K"),
    ("P_kPa", "p", "kPa"),
    ("Conc (mM)", "conc", "mM"),
    ("Flow (mL/min)", "flow", "mL/min"),
    ("sample_id", "sample id", None),
    ("SampleID", "sample id", None),
    ("Date", "date", None),
    ("Absorbance (AU)", "absorbance au", None),
])
def test_split_header(raw, name, unit):
    assert split_header(raw) == (name, unit)


def test_case_sensitive_units():
    assert token_to_unit("mM") == "millimolar"
    assert token_to_unit("mm") == "millimeter"
    assert token_to_unit("A") is None  # would be ampere; too ambiguous in headers
    assert pretty("milligram / milliliter") == "mg/mL"
    assert parse_unit("°C") == "degC"
    with pytest.raises(ValueError):
        parse_unit("bananas")


def test_normalize_keeps_ph():
    assert normalize_name("pH") == "ph"
    assert normalize_name("SampleID") == "sample id"


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
    merged.to_csv(tmp_path / "expected.csv", index=False)
    pd.testing.assert_frame_equal(pd.read_csv(tmp_path / "out.csv"), pd.read_csv(tmp_path / "expected.csv"))


def test_reader_handles_messy_exports(messy):
    df, opts = load(messy / "eu_export.csv")
    assert opts == {"sep": ";", "encoding": "cp1252", "skiprows": 3, "decimal": ","}
    assert list(df.columns) == ["Sample", "Datum", "Temp (°C)", "Conc (mM)"]
    assert df["Temp (°C)"].tolist() == [21.5, 22.1]

    df, opts = load(messy / "report.xlsx")
    assert opts == {"skiprows": 2, "sheet": "Data"}
    assert list(df.columns) == ["sample_id", "mass_mg", "pH"]

    df, opts = load(messy / "plain.tsv")
    assert opts["sep"] == "\t" and list(df.columns) == ["sample", "temp_F"]


def test_clean_script_reads_messy_files_the_same_way(messy, tmp_path):
    tables, options = {}, {}
    for p in sorted(messy.iterdir()):
        tables[p.name], options[p.name] = load(p)
    targets, _ = suggest_targets(tables)
    plan = build_plan(tables, targets, read_options=options)
    merged, _ = apply_plan(tables, plan)
    script = tmp_path / "clean.py"
    script.write_text(generate_clean_script(plan))
    subprocess.run([sys.executable, str(script), str(messy), str(tmp_path / "out.csv")], check=True)
    merged.to_csv(tmp_path / "expected.csv", index=False)
    pd.testing.assert_frame_equal(pd.read_csv(tmp_path / "out.csv"), pd.read_csv(tmp_path / "expected.csv"))
    # temp_F in the TSV is converted to Celsius alongside the °C file
    assert pd.read_csv(tmp_path / "out.csv")["temperature"].dropna().between(20, 23).all()


def test_suggest_schema_groups_columns_across_files(messy):
    tables = {p.name: load(p)[0] for p in sorted(messy.iterdir())}
    targets, notes = suggest_targets(tables)
    assert targets["sample_id"]["kind"] == "id"
    assert targets["timestamp"]["kind"] == "datetime"
    assert targets["temperature"] == {**targets["temperature"], "kind": "quantity", "unit": "degC"}
    assert targets["concentration"]["unit"] == "millimolar"
    assert targets["mass"]["unit"] == "gram"
    assert targets["ph"]["kind"] == "number" and targets["ph"]["valid_range"] == (0.0, 14.0)
    seen = {n["target"]: n for n in notes}
    assert set(seen["sample_id"]["seen_as"]) == {"Sample", "sample_id", "sample"}


def test_overrides_win_and_survive_rebuilds(tables):
    overrides = {"sensor_carol_2026-09.xlsx": {"Temp": {"unit": "degC"}, "weight": {"target": None}},
                 "lab_bob_2026-09.csv": {"mass_g": {"target": "pressure"}}}
    plan = build_plan(tables, overrides=overrides)
    carol = next(fp for fp in plan.files if fp.file.startswith("sensor_carol"))
    assert carol.mapping_for("temperature").unit_source == "user"
    assert carol.mapping_for("mass") is None and "weight" in carol.unmapped
    bob = next(fp for fp in plan.files if fp.file.startswith("lab_bob"))
    # the user's choice beats the rule match that wanted pressure_atm
    assert bob.mapping_for("pressure").source == "mass_g"
    assert bob.mapping_for("pressure").method == "user"
    merged, validation = apply_plan(tables, plan)
    assert any(i.severity == "error" and i.column == "pressure" for i in validation)  # grams aren't kPa


def test_incompatible_unit_override_is_an_error(tables):
    plan = build_plan(tables, overrides={"lab_bob_2026-09.csv": {"temperature_K": {"unit": "gram"}}})
    assert any(i.severity == "error" and i.kind == "unit_conflict" for i in plan.issues)


def test_previews_show_conversion(tables):
    plan = build_plan(tables)
    prev = column_previews(tables, plan)["sensor_carol_2026-09.xlsx"]["Temp"]
    raw, clean = float(prev[0]["raw"]), float(prev[0]["clean"])
    assert clean == pytest.approx((raw - 32) * 5 / 9, abs=0.01)


def test_schema_validation():
    clean = normalize_targets({"ph": {"kind": "number", "valid_range": [0, 14], "synonyms": "pH, acidity"},
                               "temp": {"kind": "quantity", "unit": "°C", "valid_range": [None, 100]}})
    assert clean["ph"]["synonyms"] == ["acidity", "ph"]
    assert clean["temp"]["unit"] == "degC" and clean["temp"]["valid_range"] == (float("-inf"), 100.0)
    for bad in ({"Bad Name": {"kind": "text"}}, {"x": {"kind": "quantity", "unit": "nonsense"}},
                {"x": {"kind": "number", "valid_range": [5, 1]}}, {"source_file": {"kind": "text"}}):
        with pytest.raises(SchemaError):
            normalize_targets(bad)


def test_open_ended_range_codegen(tables, tmp_path):
    targets = normalize_targets({"temperature": {"kind": "quantity", "unit": "degC", "valid_range": [None, 100],
                                                 "synonyms": ["temp"]}})
    script = generate_clean_script(build_plan(tables, targets))
    assert "float('-inf')" in script
    compile(script, "clean.py", "exec")


def test_same_script_ignores_timestamp(tables):
    plan = build_plan(tables)
    a = generate_clean_script(plan)
    b = "\"\"\"clean.py: generated by Schemata on 1999-01-01 00:00.\n" + a.split("\n", 1)[1]
    assert same_script(a, b)
    assert not same_script(a, a.replace("degF", "degC"))
