"""End-to-end tests through the HTTP API. Run from backend/:  pytest -q"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from main import create_app

H = {"X-Schemata": "1"}


@pytest.fixture
def client(tmp_path):
    app = create_app(data_dir=tmp_path / "data", watch_interval=0)
    with TestClient(app) as c:
        c.app_state = app.state
        yield c


def wait_idle(client, pid, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        p = client.get(f"/api/projects/{pid}/pipeline").json()
        if not p["running"]:
            return p
        time.sleep(0.1)
    raise AssertionError("pipeline did not finish")


def sample(client):
    r = client.post("/api/projects", json={"name": "Sample", "template": "sample"}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def test_security_guards(client):
    assert client.get("/api/health").json()["ok"]
    # state-changing calls need the X-Schemata header (CSRF guard)
    assert client.post("/api/projects", json={"name": "x"}).status_code == 403
    # and must come from our own origin
    r = client.post("/api/projects", json={"name": "x"}, headers={**H, "Origin": "http://evil.example"})
    assert r.status_code == 403
    # DNS rebinding: requests addressed to another host name are refused
    assert client.get("/api/projects", headers={"Host": "evil.example"}).status_code == 400
    r = client.get("/")
    assert "Content-Security-Policy" in r.headers


def test_sample_project_end_to_end(client):
    p = sample(client)
    pid = p["id"]
    assert len(p["files"]) == 3 and p["needs_approval"]

    plan = client.get(f"/api/projects/{pid}/plan").json()
    carol = [i for i in plan["issues"] if i["file"] == "sensor_carol_2026-09.xlsx" and i["column"] == "Temp"]
    assert carol and carol[0]["severity"] == "warning"
    assert plan["previews"]["sensor_carol_2026-09.xlsx"]["Temp"][0]["clean"]

    # running before approval is refused with a helpful message
    r = client.post(f"/api/projects/{pid}/pipeline/run", json={}, headers=H)
    assert r.status_code == 409 and "approve" in r.json()["detail"]

    assert client.post(f"/api/projects/{pid}/approve", headers=H).status_code == 200
    pipe = wait_idle(client, pid)
    assert pipe["stale"] == []
    assert [s["state"] for s in pipe["graph"]["steps"]] == ["fresh"] * 4

    res = client.get(f"/api/projects/{pid}/results?limit=5&sort=temperature&desc=true").json()
    assert res["total"] == 36 and len(res["rows"]) == 5
    temps = [float(r[res["columns"].index("temperature")]) for r in res["rows"]]
    assert temps == sorted(temps, reverse=True)
    assert client.get(f"/api/projects/{pid}/results?q=C-00").json()["filtered"] == 9

    svg = client.get(f"/api/projects/{pid}/outputs/figures/temp_by_file.svg")
    assert svg.status_code == 200 and svg.text.startswith("<svg") and "sandbox" in svg.headers["content-security-policy"]
    assert client.get(f"/api/projects/{pid}/outputs/../schema.json").status_code == 404

    # second run: nothing changed, everything skipped, time saved recorded
    r = client.post(f"/api/projects/{pid}/pipeline/run", json={"wait": True}, headers=H).json()
    assert r["last_report"]["ran"] == [] and r["last_report"]["time_saved_s"] > 0
    runs = client.get(f"/api/projects/{pid}/runs").json()
    assert len(runs) == 2 and runs[0]["trigger"] == "manual"


def test_new_file_needs_review_and_drift(client, tmp_path):
    pid = sample(client)["id"]
    client.post(f"/api/projects/{pid}/approve", headers=H)
    wait_idle(client, pid)

    csv = "Sample ID,Date,Temp (C),Mass (mg),Pressure (kPa),Humidity\nD-001,10/01/2026 09:00,22.1,2500,101.2,40\n"
    r = client.post(f"/api/projects/{pid}/files", files=[("files", ("lab_dana_2026-10.csv", csv.encode()))],
                    headers=H).json()
    assert r["saved"] == ["lab_dana_2026-10.csv"]
    summary = client.get(f"/api/projects/{pid}").json()
    assert summary["needs_approval"] and summary["unapproved"] == ["lab_dana_2026-10.csv"]
    plan = client.get(f"/api/projects/{pid}/plan").json()
    drift = [i for i in plan["issues"] if i["kind"] == "drift"]
    assert any("Humidity" in i["message"] for i in drift)

    client.post(f"/api/projects/{pid}/approve", headers=H)
    pipe = wait_idle(client, pid)
    assert pipe["stale"] == []
    assert client.get(f"/api/projects/{pid}/results").json()["total"] == 37


def test_rejects_unreadable_and_unsafe_uploads(client):
    pid = client.post("/api/projects", json={"name": "Up"}, headers=H).json()["id"]
    files = [("files", ("old.xls", b"\xd0\xcf\x11\xe0")), ("files", ("empty.csv", b"")),
             ("files", ("../../etc/passwd.csv", b"a,b\n1,2\n")), ("files", ("notes.pdf", b"%PDF"))]
    r = client.post(f"/api/projects/{pid}/files", files=files, headers=H).json()
    reasons = {x["file"]: x["reason"] for x in r["rejected"]}
    assert ".xlsx" in reasons["old.xls"] and "empty" in reasons["empty.csv"] and "notes.pdf" in reasons
    # path components are stripped: the file lands safely in raw/ as passwd.csv
    assert r["saved"] == ["passwd.csv"]


def test_overrides_are_audited_and_undoable(client):
    pid = sample(client)["id"]
    ov = {"sensor_carol_2026-09.xlsx": {"Temp": {"unit": "degC"}}}
    plan = client.put(f"/api/projects/{pid}/overrides", json={"overrides": ov}, headers=H).json()
    assert plan["overrides"] == ov
    events = client.get(f"/api/projects/{pid}/activity").json()
    entry = next(e for e in events if e["kind"] == "mapping")
    assert entry["detail"]["previous"] == "<auto>" and entry["detail"]["value"] == "degC"
    plan = client.put(f"/api/projects/{pid}/overrides", json={"overrides": {}}, headers=H).json()
    carol = next(f for f in plan["plan"]["files"] if f["file"].startswith("sensor_carol"))
    temp = next(m for m in carol["mappings"] if m["target"] == "temperature")
    assert temp["unit"] == "degF"


def test_schema_suggest_and_validation(client):
    pid = client.post("/api/projects", json={"name": "Blank", "template": "blank"}, headers=H).json()["id"]
    assert client.post(f"/api/projects/{pid}/schema/suggest", headers=H).status_code == 409
    sample_dir = Path(__file__).resolve().parents[2] / "sample_data"
    files = [("files", (p.name, p.read_bytes())) for p in sorted(sample_dir.iterdir()) if p.suffix in (".csv", ".xlsx")]
    client.post(f"/api/projects/{pid}/files", files=files, headers=H)
    sug = client.post(f"/api/projects/{pid}/schema/suggest", headers=H).json()
    assert {"sample_id", "timestamp", "temperature", "mass", "pressure"} <= set(sug["targets"])
    r = client.put(f"/api/projects/{pid}/schema", json={"targets": sug["targets"]}, headers=H)
    assert r.status_code == 200
    bad = client.put(f"/api/projects/{pid}/schema", json={"targets": {"x": {"kind": "quantity", "unit": "zz"}}},
                     headers=H)
    assert bad.status_code == 422 and "zz" in bad.json()["detail"]


def test_steps_scripts_and_selective_rerun(client):
    pid = sample(client)["id"]
    client.post(f"/api/projects/{pid}/approve", headers=H)
    wait_idle(client, pid)

    script = (b"import sys, pandas as pd\n"
              b"df = pd.read_csv(sys.argv[1])\n"
              b"pd.DataFrame({'rows': [len(df)]}).to_csv(sys.argv[2], index=False)\n")
    assert client.post(f"/api/projects/{pid}/scripts", files={"file": ("count.py", script)},
                       headers=H).status_code == 200
    steps = client.get(f"/api/projects/{pid}/pipeline").json()["graph"]["steps"]
    cfg = [s["config"] for s in steps if s["config"]]
    cfg.append({"name": "count", "kind": "script",
                "params": {"script": "count.py", "outputs": ["output/count.csv"]}})
    cfg.append({"name": "mass_vs_temp", "kind": "chart",
                "params": {"chart": "scatter", "x": "temperature", "y": "mass"}})
    r = client.put(f"/api/projects/{pid}/pipeline/steps", json={"steps": cfg}, headers=H)
    assert r.status_code == 200, r.text
    assert sorted(r.json()["stale"]) == ["count", "mass_vs_temp"]

    r = client.post(f"/api/projects/{pid}/pipeline/run", json={"wait": True}, headers=H).json()
    assert sorted(r["last_report"]["ran"]) == ["count", "mass_vs_temp"]
    out = client.get(f"/api/projects/{pid}/outputs/count.csv").text
    assert out.strip().splitlines()[-1] == "36"

    bad = client.put(f"/api/projects/{pid}/pipeline/steps",
                     json={"steps": [{"name": "x", "kind": "script", "params": {"script": "count.py",
                                                                               "outputs": ["../x.csv"]}}]},
                     headers=H)
    assert bad.status_code == 422


def test_downloads_and_bundle(client):
    pid = sample(client)["id"]
    client.post(f"/api/projects/{pid}/approve", headers=H)
    wait_idle(client, pid)
    csv = client.get(f"/api/projects/{pid}/download/merged.csv")
    assert csv.status_code == 200 and csv.text.startswith("source_file,")
    xlsx = client.get(f"/api/projects/{pid}/download/merged.xlsx")
    assert len(pd.read_excel(io.BytesIO(xlsx.content))) == 36
    assert "def main()" in client.get(f"/api/projects/{pid}/download/clean.py").text

    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/projects/{pid}/download/bundle.zip").content))
    names = set(z.namelist())
    assert {"clean.py", "MANIFEST.json", "README.txt", "activity.csv", "output/merged.csv",
            "raw/lab_alice_2026-09.csv"} <= names
    manifest = json.loads(z.read("MANIFEST.json"))
    assert all(len(f["sha256"]) == 64 for f in manifest["files"])


def test_auto_run_picks_up_dropped_files(client):
    pid = sample(client)["id"]
    client.patch(f"/api/projects/{pid}", json={"auto_run": True}, headers=H)
    client.post(f"/api/projects/{pid}/approve", headers=H)
    wait_idle(client, pid)
    watcher = client.app_state.watcher
    ws = client.app_state.workspace

    # a clean new file dropped straight into raw/ is approved and run without anyone clicking
    raw = ws.paths(pid).raw
    (raw / "lab_alice_2026-10.csv").write_text(
        "Sample ID,Date,Temp (C),Mass (mg),Pressure (kPa)\nA-101,10/02/2026 14:00,23.0,2400,101.5\n")
    assert watcher.tick() == {pid: "approved"}
    wait_idle(client, pid)
    assert client.get(f"/api/projects/{pid}/results").json()["total"] == 37

    # a file with problems waits for review instead
    (raw / "mystery.csv").write_text("Sample ID,Date,Temp\nM-1,10/03/2026 10:00,300\n")
    assert watcher.tick() == {pid: "waiting"}
    assert client.get(f"/api/projects/{pid}").json()["needs_approval"]
    assert any("waiting for review" in e["message"] for e in client.get(f"/api/projects/{pid}/activity").json())


def test_delete_project(client):
    pid = sample(client)["id"]
    root = client.app_state.workspace.paths(pid).root
    assert root.exists()
    assert client.delete(f"/api/projects/{pid}", headers=H).status_code == 204
    assert not root.exists()
    assert client.get(f"/api/projects/{pid}").status_code == 404
