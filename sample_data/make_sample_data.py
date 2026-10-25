"""Generate messy demo files for Schemata.

Three "lab members" record the same experiment differently:
  - alice: Celsius, milligrams, kPa, US dates (MM/DD/YYYY)
  - bob:   Kelvin, grams, atm, ISO timestamps
  - carol: an unlabeled "Temp" column that is secretly Fahrenheit,
           grams, kPa, dates like 24-Sep-2026 (Excel file)

Run:  python sample_data/make_sample_data.py
"""

from pathlib import Path
import random

import pandas as pd

OUT = Path(__file__).parent
random.seed(42)

N = 12
base = pd.Timestamp("2026-09-01 09:00")


def temps_c():
    return [round(random.uniform(20.0, 26.0), 2) for _ in range(N)]


def masses_g():
    return [round(random.uniform(1.2, 3.8), 4) for _ in range(N)]


def pressures_kpa():
    return [round(random.uniform(99.0, 103.0), 2) for _ in range(N)]


def times(offset_days):
    return [base + pd.Timedelta(days=offset_days + i, hours=random.randint(0, 6)) for i in range(N)]


# Alice: Celsius, mg, kPa, MM/DD/YYYY
t = times(0)
alice = pd.DataFrame({
    "Sample ID": [f"A-{i:03d}" for i in range(1, N + 1)],
    "Date": [d.strftime("%m/%d/%Y %H:%M") for d in t],
    "Temp (C)": temps_c(),
    "Mass (mg)": [round(g * 1000, 1) for g in masses_g()],
    "Pressure (kPa)": pressures_kpa(),
})
alice.to_csv(OUT / "lab_alice_2026-09.csv", index=False)

# Bob: Kelvin, grams, atm, ISO timestamps
t = times(3)
bob = pd.DataFrame({
    "sample_id": [f"B-{i:03d}" for i in range(1, N + 1)],
    "timestamp": [d.isoformat() for d in t],
    "temperature_K": [round(c + 273.15, 2) for c in temps_c()],
    "mass_g": masses_g(),
    "pressure_atm": [round(p / 101.325, 4) for p in pressures_kpa()],
})
bob.to_csv(OUT / "lab_bob_2026-09.csv", index=False)

# Carol: "Temp" with no unit but the values are Fahrenheit. Excel file.
t = times(6)
carol = pd.DataFrame({
    "SampleID": [f"C-{i:03d}" for i in range(1, N + 1)],
    "Collected": [d.strftime("%d-%b-%Y %H:%M") for d in t],
    "Temp": [round(c * 9 / 5 + 32, 1) for c in temps_c()],
    "weight": masses_g(),
    "P_kPa": pressures_kpa(),
})
carol.to_excel(OUT / "sensor_carol_2026-09.xlsx", index=False)

print("Wrote:", *sorted(p.name for p in OUT.iterdir() if p.suffix in {".csv", ".xlsx"}), sep="\n  ")
