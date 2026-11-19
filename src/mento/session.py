"""The recording session shared by the CLI, the tray and the recorder process.

The recorder process is the only writer of state.json, which it refreshes
several times a second (heartbeat). The CLI sends it orders through the
control file. A stale heartbeat means the recorder died: the session is
"interrupted" and `stop` finalizes what was captured.
"""

import json
import os
import time
from pathlib import Path

HEARTBEAT_TIMEOUT = 5  # seconds


class Session:
    def __init__(self, state_dir):
        self.dir = Path(state_dir)
        self.state_file = self.dir / "state.json"
        self.control_file = self.dir / "control"

    # ── Read side (CLI, tray) ────────────────────────────────────────────────

    def read(self):
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def status(self):
        """idle | starting | recording | paused | stopping | failed | interrupted"""
        st = self.read()
        if st is None:
            return "idle"
        if st["status"] == "failed":
            return "failed"
        if time.time() - st["heartbeat"] > HEARTBEAT_TIMEOUT:
            return "interrupted"
        return st["status"]

    def elapsed(self, st=None):
        """Seconds recorded so far, pauses excluded."""
        st = st or self.read()
        if not st:
            return 0
        seconds = st.get("recorded_s", 0)
        if st["status"] == "recording" and st.get("segment_start"):
            seconds += st["heartbeat"] - st["segment_start"]
        return int(seconds)

    def send(self, command):
        tmp = self.control_file.with_suffix(".tmp")
        tmp.write_text(command, encoding="utf-8")
        os.replace(tmp, self.control_file)

    def wait_for(self, predicate, timeout):
        """Polls the state until predicate(status) holds; returns the last status."""
        deadline = time.time() + timeout
        while True:
            status = self.status()
            if predicate(status) or time.time() > deadline:
                return status
            time.sleep(0.1)

    def clear(self):
        for path in (self.state_file, self.control_file):
            path.unlink(missing_ok=True)

    # ── Write side (recorder process) ────────────────────────────────────────

    def write(self, **state):
        state["heartbeat"] = time.time()
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, self.state_file)

    def take_command(self):
        try:
            command = self.control_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        self.control_file.unlink(missing_ok=True)
        return command or None
