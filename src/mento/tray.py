"""Panel icon: start, pause, resume, stop and recent recordings.

Linux runs tray_appindicator.py under the system Python (PyGObject and
AppIndicator are system packages there); Windows and macOS use pystray.
Both only drive the CLI, so the icon and the terminal always agree.
"""

import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import state_dir

HERE = Path(__file__).resolve().parent
SYSTEM_PYTHON = "/usr/bin/python3"


def cli_command():
    """The CLI of this very install (the script next to this Python)."""
    scripts = Path(sys.executable).parent
    exe = scripts / ("mento.exe" if sys.platform == "win32" else "mento")
    return [str(exe)] if exe.exists() else [sys.executable, "-m", "mento"]


def main():
    if sys.platform.startswith("linux"):
        return run_linux()
    return run_pystray()


def run_linux():
    python = SYSTEM_PYTHON if Path(SYSTEM_PYTHON).exists() else shutil.which("python3")
    probe = subprocess.run([python, "-c", "import gi; gi.require_version('AyatanaAppIndicator3', '0.1')"],
                           capture_output=True)
    if probe.returncode != 0:
        print("✗ AppIndicator is missing: sudo apt install gir1.2-ayatanaappindicator3-0.1 "
              "(GNOME also needs the AppIndicator extension, built in on Ubuntu)", file=sys.stderr)
        return 1
    return subprocess.call([python, "-I", str(HERE / "tray_appindicator.py"), *cli_command()])


def single_instance():
    """Holds a lock for the life of the process; False if another icon runs."""
    handle = open(state_dir() / "tray.lock", "a+")  # noqa: SIM115 — held until exit
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    single_instance.handle = handle
    return True


def run_pystray():
    import pystray
    from PIL import Image

    from .cli import open_path
    from .tray_core import Model

    if not single_instance():
        print("mento tray is already running", file=sys.stderr)
        return 0

    model = Model(cli_command())
    images = {}

    def image(name):
        if name not in images:
            images[name] = Image.open(HERE / "icons" / f"{name}.png")
        return images[name]

    def action(name):
        def run(icon, _item):
            def work():
                ok, msg = model.run(name)
                if not ok:
                    icon.notify(msg, "mento")
                update(icon)
            threading.Thread(target=work, daemon=True).start()
        return run

    def recent_items():
        rows = model.recent()
        if not rows:
            return [pystray.MenuItem("None", None, enabled=False)]
        return [pystray.MenuItem(f"{r['id']}  ({r['state']})",
                                 lambda _i, _it, p=r["path"]: open_path(p)) for r in rows]

    def update(icon):
        model.refresh()
        icon.icon = image(model.icon_name())
        icon.title = f"mento — {model.summary()}"
        warning = model.new_warning()
        if warning:
            icon.notify(warning, "mento")
        icon.update_menu()

    menu = pystray.Menu(
        pystray.MenuItem(lambda _: model.summary(), None, enabled=False),
        pystray.Menu.SEPARATOR,
        *(pystray.MenuItem(label, action(name), visible=lambda _, n=name: model.visible(n))
          for name, label in (("start", "Start recording"), ("pause", "Pause"),
                              ("resume", "Resume"), ("stop", "Stop"))),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Recent recordings", pystray.Menu(recent_items)),
        pystray.MenuItem("Open last recording", action("open")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit icon", lambda icon, _: icon.stop()),
    )
    icon = pystray.Icon("mento", image("mento-idle"), "mento", menu)

    def poll(icon):
        icon.visible = True
        while icon.visible:
            update(icon)
            time.sleep(1)

    icon.run(setup=poll)
    return 0


if __name__ == "__main__":
    sys.exit(main())
