"""What every panel icon shares: it runs the CLI and shows `status --json`.

No third-party import here: on Linux this module is loaded by the system
Python running tray_appindicator.py, outside the package's environment.
"""

import json
import subprocess
import sys

RECENT = 5
LABELS = {"idle": "Idle", "starting": "Starting…", "recording": "Recording", "paused": "Paused",
          "stopping": "Encoding…", "interrupted": "Interrupted — click Stop to recover",
          "failed": "Failed — click Stop to recover", "error": "Error"}
ICONS = {"idle": "idle", "starting": "recording", "recording": "recording", "paused": "paused",
         "stopping": "paused", "interrupted": "interrupted", "failed": "interrupted",
         "error": "interrupted"}
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


def fmt(seconds):
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class Model:
    def __init__(self, cli):
        self.cli = list(cli)
        self.state = {"state": "idle"}
        self.last_warning = None

    def call(self, *args):
        return subprocess.run([*self.cli, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", creationflags=NO_WINDOW)

    def refresh(self):
        res = self.call("status", "--json")
        if res.returncode != 0:
            self.state = {"state": "error", "error": res.stderr.strip()[:80]}
        else:
            self.state = json.loads(res.stdout)
        return self.state

    @property
    def status(self):
        return self.state["state"]

    def icon_name(self):
        return f"audio-recorder-{ICONS[self.status]}"

    def elapsed(self):
        return fmt(self.state.get("elapsed_s", 0)) if self.status in ("recording", "paused") else ""

    def summary(self):
        text = LABELS[self.status]
        if self.status == "error":
            return f"Error: {self.state.get('error', '')}"
        if "id" in self.state:
            text += f" · {fmt(self.state.get('elapsed_s', 0))} · {self.state['id']}"
        return text

    def new_warning(self):
        """The recorder's warning, once each time it changes."""
        warning = self.state.get("warning")
        if warning and warning != self.last_warning:
            self.last_warning = warning
            return warning
        if not warning:
            self.last_warning = None
        return None

    def visible(self, action):
        st = self.status
        return {"start": st == "idle", "pause": st == "recording", "resume": st == "paused",
                "stop": st in ("recording", "paused", "interrupted", "failed")}[action]

    def run(self, action):
        """(ok, message) of a CLI action."""
        res = self.call(action)
        msg = (res.stderr or res.stdout).strip().splitlines()
        return res.returncode == 0, (msg[-1] if msg else action)

    def recent(self):
        res = self.call("list", "--json", "--limit", str(RECENT))
        return json.loads(res.stdout) if res.returncode == 0 else []
