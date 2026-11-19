#!/usr/bin/env python3
"""Panel icon for audio-recorder (Linux, AppIndicator).

It holds no recording logic: every action runs the CLI, and the state shown
comes from `audio-recorder status --json`, polled every second, so the icon
and the terminal always agree whichever one started the recording.

Usage: tray.py <path to the audio-recorder launcher>
"""

import fcntl
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
from gi.repository import GLib, Gtk  # noqa: E402

ICONS = Path(__file__).resolve().parent.parent / "icons"
RECENT = 5
LABELS = {"idle": "Idle", "recording": "Recording", "paused": "Paused",
          "interrupted": "Interrupted — ffmpeg stopped"}


def fmt(seconds):
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class Tray:
    def __init__(self, cli):
        self.cli = cli
        self.state = None
        self.busy = False

        self.indicator = AppIndicator.Indicator.new(
            "audio-recorder", "audio-recorder-idle",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_icon_theme_path(str(ICONS))
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

        menu = Gtk.Menu()
        self.status_item = Gtk.MenuItem(label="…", sensitive=False)
        self.start_item = self.item("Start recording", "start")
        self.pause_item = self.item("Pause", "pause")
        self.resume_item = self.item("Resume", "resume")
        self.stop_item = self.item("Stop", "stop")
        self.recent_item = Gtk.MenuItem(label="Recent recordings")
        open_last = self.item("Open last recording", "open")
        quit_item = Gtk.MenuItem(label="Quit icon")
        quit_item.connect("activate", lambda _: Gtk.main_quit())
        for it in (self.status_item, Gtk.SeparatorMenuItem(), self.start_item,
                   self.pause_item, self.resume_item, self.stop_item,
                   Gtk.SeparatorMenuItem(), self.recent_item, open_last,
                   Gtk.SeparatorMenuItem(), quit_item):
            menu.append(it)
        menu.show_all()
        self.indicator.set_menu(menu)

        self.refresh_recent()
        self.tick()
        GLib.timeout_add_seconds(1, self.tick)
        GLib.timeout_add_seconds(30, self.refresh_recent)

    def item(self, label, command):
        it = Gtk.MenuItem(label=label)
        it.connect("activate", lambda _: self.run_bg([command]))
        return it

    # ── CLI ──────────────────────────────────────────────────────────────────

    def call(self, args):
        return subprocess.run([self.cli, *args], capture_output=True, text=True)

    def run_bg(self, args):
        """Runs a CLI command off the UI thread; failures become a notification."""
        self.busy = True
        self.update_menu()

        def work():
            res = self.call(args)
            if res.returncode != 0:
                msg = (res.stderr or res.stdout).strip().splitlines()
                subprocess.run(["notify-send", "audio-recorder",
                                msg[-1] if msg else f"{args[0]} failed"])
            GLib.idle_add(self.done)

        threading.Thread(target=work, daemon=True).start()

    def done(self):
        self.busy = False
        self.tick()
        self.refresh_recent()
        return False

    # ── State ────────────────────────────────────────────────────────────────

    def tick(self):
        res = self.call(["status", "--json"])
        if res.returncode != 0:
            self.state = {"state": "error", "error": res.stderr.strip()}
        else:
            self.state = json.loads(res.stdout)
        st = self.state["state"]
        if st == "error":
            self.indicator.set_icon_full("audio-recorder-interrupted", "error")
            self.indicator.set_label("", "")
            self.status_item.set_label(f"Error: {self.state['error'][:60]}")
        else:
            self.indicator.set_icon_full(f"audio-recorder-{st}", LABELS[st])
            elapsed = fmt(self.state.get("elapsed_s", 0))
            self.indicator.set_label(elapsed if st in ("recording", "paused") else "", "00:00:00")
            text = LABELS[st]
            if st != "idle":
                text += f" · {elapsed} · {self.state['id']}"
            self.status_item.set_label(text)
        self.update_menu()
        return True

    def update_menu(self):
        st = self.state["state"] if self.state else "idle"
        free = not self.busy
        self.start_item.set_visible(st == "idle")
        self.start_item.set_sensitive(free)
        self.pause_item.set_visible(st == "recording")
        self.pause_item.set_sensitive(free)
        self.resume_item.set_visible(st == "paused")
        self.resume_item.set_sensitive(free)
        self.stop_item.set_visible(st in ("recording", "paused", "interrupted"))
        self.stop_item.set_sensitive(free)

    def refresh_recent(self):
        res = self.call(["list", "--json", "--limit", str(RECENT)])
        sub = Gtk.Menu()
        rows = json.loads(res.stdout) if res.returncode == 0 else []
        for row in rows:
            it = Gtk.MenuItem(label=f"{row['id']}  ({row['state']})")
            it.connect("activate", lambda _, p=row["path"]: subprocess.Popen(["xdg-open", p]))
            sub.append(it)
        if not rows:
            sub.append(Gtk.MenuItem(label="None", sensitive=False))
        sub.show_all()
        self.recent_item.set_submenu(sub)
        return True


def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 1
    # One icon only: a second launch (menu, autostart) exits quietly
    lock_path = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "audio-recorder-tray.lock"
    lock = open(lock_path, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("audio-recorder tray is already running", file=sys.stderr)
        return 0
    Tray(sys.argv[1])
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
