#!/usr/bin/env python3
"""Panel icon on Linux (AppIndicator, GNOME/KDE/XFCE panels).

Runs under the system Python, which has PyGObject and AppIndicator, and
drives the CLI through tray_core. Usage: tray_appindicator.py <cli> [args…]
"""

import fcntl
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tray_core import Model  # noqa: E402

ICONS = HERE / "icons"


class Tray:
    def __init__(self, model):
        self.model = model
        self.busy = False

        self.indicator = AppIndicator.Indicator.new(
            "audio-recorder", "audio-recorder-idle",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_icon_theme_path(str(ICONS))
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

        menu = Gtk.Menu()
        self.status_item = Gtk.MenuItem(label="…", sensitive=False)
        self.actions = {name: self.item(label, name) for name, label in (
            ("start", "Start recording"), ("pause", "Pause"), ("resume", "Resume"), ("stop", "Stop"))}
        self.recent_item = Gtk.MenuItem(label="Recent recordings")
        open_last = self.item("Open last recording", "open")
        quit_item = Gtk.MenuItem(label="Quit icon")
        quit_item.connect("activate", lambda _: Gtk.main_quit())
        for it in (self.status_item, Gtk.SeparatorMenuItem(), *self.actions.values(),
                   Gtk.SeparatorMenuItem(), self.recent_item, open_last,
                   Gtk.SeparatorMenuItem(), quit_item):
            menu.append(it)
        menu.show_all()
        self.indicator.set_menu(menu)

        self.refresh_recent()
        self.tick()
        GLib.timeout_add_seconds(1, self.tick)
        GLib.timeout_add_seconds(30, self.refresh_recent)

    def item(self, label, action):
        it = Gtk.MenuItem(label=label)
        it.connect("activate", lambda _: self.run_bg(action))
        return it

    def run_bg(self, action):
        self.busy = True
        self.update_menu()

        def work():
            ok, msg = self.model.run(action)
            if not ok:
                subprocess.run(["notify-send", "audio-recorder", msg])
            GLib.idle_add(self.done)

        threading.Thread(target=work, daemon=True).start()

    def done(self):
        self.busy = False
        self.tick()
        self.refresh_recent()
        return False

    def tick(self):
        model = self.model
        model.refresh()
        self.indicator.set_icon_full(model.icon_name(), model.status)
        self.indicator.set_label(model.elapsed(), "00:00:00")
        self.status_item.set_label(model.summary())
        warning = model.new_warning()
        if warning:
            subprocess.run(["notify-send", "audio-recorder", warning])
        self.update_menu()
        return True

    def update_menu(self):
        for name, it in self.actions.items():
            it.set_visible(self.model.visible(name))
            it.set_sensitive(not self.busy)

    def refresh_recent(self):
        sub = Gtk.Menu()
        rows = self.model.recent()
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
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 1
    # One icon only: a second launch (menu, autostart) exits quietly
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or Path.home() / ".cache")
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(runtime / "audio-recorder-tray.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    lock = os.fdopen(fd, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("audio-recorder tray is already running", file=sys.stderr)
        return 0
    Tray(Model(sys.argv[1:]))
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
