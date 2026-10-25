"""Background watcher: picks up files dropped into raw/ and auto-runs.

Every few seconds, for each project with auto-run on, it calls
Workspace.auto_tick(): new or changed files are registered, files that map
cleanly are approved, and stale steps run. Anything that needs a human (a
unit conflict, schema drift, an error) is left for review and logged once.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("schemata.watcher")


class Watcher:
    def __init__(self, workspace, interval_s: float = 3.0):
        self.ws = workspace
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread = None
        self._memo: dict = {}

    def start(self) -> None:
        if self._thread is None and self.interval_s > 0:
            self._thread = threading.Thread(target=self._loop, name="schemata-watcher", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def tick(self) -> dict:
        done = {}
        for p in self.ws.store.projects():
            if not p["auto_run"]:
                continue
            try:
                result = self.ws.auto_tick(p["id"], self._memo)
            except Exception:  # one broken project must not stop the watcher for the others
                log.exception("auto-run failed for project %s", p["id"])
                continue
            if result:
                done[p["id"]] = result
        return done

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.tick()
