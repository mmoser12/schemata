"""Target schema: the clean columns every input file gets mapped onto.

Edit TARGETS to fit a different experiment. Each target has:
  kind         "id" | "datetime" | "quantity" | "text"
  unit         canonical unit for quantities (pint syntax)
  synonyms     header names that map here by rule (normalized: lowercase, words)
  valid_range  (min, max) in the canonical unit, used for validation checks
"""

TARGETS: dict[str, dict] = {
    "sample_id": {
        "kind": "id",
        "synonyms": ["sample id", "sampleid", "sample", "id", "sample no", "sample number", "specimen"],
    },
    "timestamp": {
        "kind": "datetime",
        "synonyms": ["timestamp", "date", "time", "datetime", "date time", "collected",
                     "collection date", "measured at", "measurement time"],
    },
    "temperature": {
        "kind": "quantity",
        "unit": "degC",
        "synonyms": ["temperature", "temp", "t"],
        "valid_range": (-50.0, 150.0),
    },
    "mass": {
        "kind": "quantity",
        "unit": "g",
        "synonyms": ["mass", "weight", "wt", "sample mass"],
        "valid_range": (0.0, 10_000.0),
    },
    "pressure": {
        "kind": "quantity",
        "unit": "kPa",
        "synonyms": ["pressure", "p", "press", "pres"],
        "valid_range": (50.0, 200.0),
    },
}
