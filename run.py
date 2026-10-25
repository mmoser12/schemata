"""Start Schemata and open it in your browser.

    python run.py                      # http://127.0.0.1:8000 (or the next free port)
    python run.py --port 9000          # pick a port
    python run.py --data ~/lab-data    # keep projects somewhere else (default: ./data)
    python run.py --no-browser         # don't open a browser tab

Schemata is a local app: it only listens on this computer unless you pass
--host with --allow-remote (read the README's security notes first, because
pipeline steps run Python scripts).
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.dont_write_bytecode = True  # don't scatter __pycache__ folders through the source tree
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _free_port(host: str, preferred: int) -> int:
    for port in [preferred, *range(preferred + 1, preferred + 20)]:
        with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    raise SystemExit(f"No free port found near {preferred}; pass --port.")


def main() -> None:
    if sys.version_info < (3, 12):
        raise SystemExit(f"Schemata needs Python 3.12 or newer (this is {sys.version.split()[0]}).")

    ap = argparse.ArgumentParser(description="Run the Schemata app.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--data", default=os.environ.get("SCHEMATA_DATA_DIR") or str(ROOT / "data"),
                    help="folder for projects and the database (default: ./data)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--allow-remote", action="store_true",
                    help="allow --host to be a non-local address (anyone who can reach it can run pipelines)")
    args = ap.parse_args()

    if args.host not in LOOPBACK and not args.allow_remote:
        raise SystemExit("Refusing to listen on a non-local address without --allow-remote. "
                         "Anyone who can reach this port could run pipeline scripts on this machine.")

    try:
        import fastapi  # noqa: F401
        import pandas  # noqa: F401
        import pint  # noqa: F401
        import uvicorn
    except ImportError as e:
        raise SystemExit(f"Missing dependency ({e.name}). Install them with:\n"
                         f"    python -m pip install -r {ROOT / 'requirements.txt'}")

    data = Path(args.data).expanduser().resolve()
    os.environ["SCHEMATA_DATA_DIR"] = str(data)
    if args.host not in LOOPBACK:
        os.environ["SCHEMATA_ALLOWED_HOSTS"] = ",".join(
            filter(None, [os.environ.get("SCHEMATA_ALLOWED_HOSTS"), args.host]))
    sys.path.insert(0, str(ROOT / "backend"))
    from main import app  # noqa: E402  (reads SCHEMATA_DATA_DIR on import)

    port = _free_port(args.host, args.port)
    shown = "localhost" if args.host in LOOPBACK else args.host
    url = f"http://{shown}:{port}/"
    print(f"\n  Schemata is running at {url}\n  Projects are stored in {data}\n  Press Ctrl+C to stop.\n", flush=True)
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
