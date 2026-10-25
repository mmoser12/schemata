"""Shared test setup: import path, and keep test data out of the repo's data/ folder."""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# main.py builds a module-level app on import; point it at a throwaway folder.
if "SCHEMATA_DATA_DIR" not in os.environ:
    _tmp = tempfile.mkdtemp(prefix="schemata-test-")
    atexit.register(shutil.rmtree, _tmp, ignore_errors=True)
    os.environ["SCHEMATA_DATA_DIR"] = _tmp
os.environ.setdefault("SCHEMATA_WATCH_INTERVAL", "0")
