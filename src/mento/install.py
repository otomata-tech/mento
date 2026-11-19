"""Desktop integration and the Claude Code plugin.

- Panel icon started at login and an app menu entry: a .desktop file on
  Linux, Startup and Start Menu shortcuts on Windows, a LaunchAgent on macOS.
- The mento plugin (skill) enabled in every Claude Code config dir
  (~/.claude, ~/.claude-<account>), from the GitHub marketplace.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .tray import HERE, cli_command

MARKETPLACE = "AlexisLaporte/mento"
PLUGIN = "mento@mento"
LAUNCH_AGENT = Path.home() / "Library/LaunchAgents/cc.mento.tray.plist"


def tray_command():
    scripts = Path(sys.executable).parent
    if sys.platform == "win32":
        return [str(scripts / "mento-tray.exe")]
    gui = scripts / "mento-tray"
    return [str(gui)] if gui.exists() else [*cli_command(), "tray"]


# ── Desktop ──────────────────────────────────────────────────────────────────

def linux_files():
    return (Path.home() / ".local/share/applications/mento.desktop",
            Path.home() / ".config/autostart/mento-tray.desktop")


def install_desktop(out):
    command = tray_command()
    if sys.platform.startswith("linux"):
        entry = "\n".join([
            "[Desktop Entry]", "Type=Application", "Name=Mento",
            "Comment=Record meetings: panel icon to start, pause and stop",
            f"Exec={' '.join(command)}", f"Icon={HERE / 'icons' / 'mento.svg'}",
            "Categories=AudioVideo;Audio;", "Keywords=record;meeting;transcript;", ""])
        app, autostart = linux_files()
        for path in (app, autostart):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(entry + ("X-GNOME-Autostart-enabled=true\n" if path == autostart else ""))
        out(f"✓ App menu entry and login item ({autostart})")
    elif sys.platform == "win32":
        icon = HERE / "icons" / "mento.png"
        for folder in ("Startup", "."):
            link = windows_programs() / folder / "Mento.lnk"
            ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LINK);"
                  "$s.TargetPath=$env:TARGET;$s.IconLocation=$env:ICON;$s.Save()")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                           env={**os.environ, "LINK": str(link),
                                "TARGET": command[0], "ICON": str(icon)})
        out("✓ Start Menu entry and Startup shortcut")
    elif sys.platform == "darwin":
        args = "".join(f"<string>{a}</string>" for a in command)
        LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
        LAUNCH_AGENT.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>'
            "<key>Label</key><string>cc.mento.tray</string>"
            f"<key>ProgramArguments</key><array>{args}</array>"
            "<key>RunAtLoad</key><true/></dict></plist>\n")
        out(f"✓ Login item ({LAUNCH_AGENT})")


def uninstall_desktop(out):
    if sys.platform.startswith("linux"):
        paths = linux_files()
    elif sys.platform == "win32":
        paths = [windows_programs() / f / "Mento.lnk" for f in ("Startup", ".")]
    else:
        paths = [LAUNCH_AGENT]
    for path in paths:
        Path(path).unlink(missing_ok=True)
    out("✓ Desktop integration removed")


def windows_programs():
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"


# ── Claude Code plugin ───────────────────────────────────────────────────────

def claude_dirs():
    home = Path.home()
    return [d for d in [home / ".claude", *sorted(home.glob(".claude-*"))]
            if (d / "settings.json").exists()]


def claude(config_dir, *args):
    return subprocess.run(["claude", *args], capture_output=True, text=True,
                          env={**os.environ, "CLAUDE_CONFIG_DIR": str(config_dir)})


def install_plugin(out):
    if not shutil.which("claude"):
        out(f"! Claude Code not found — later: /plugin install mento --marketplace {MARKETPLACE}")
        return
    for d in claude_dirs():
        markets = json.loads(claude(d, "plugin", "marketplace", "list", "--json").stdout or "[]")
        mine = next((m for m in markets if m["name"] == "mento"), None)
        if mine and (mine.get("source") != "github" or mine.get("repo") != MARKETPLACE):
            claude(d, "plugin", "marketplace", "remove", "mento")  # local or former repo
            mine = None
        if mine:
            claude(d, "plugin", "marketplace", "update", "mento")
        else:
            claude(d, "plugin", "marketplace", "add", MARKETPLACE)
        installed = json.loads(claude(d, "plugin", "list", "--json").stdout or "[]")
        if any(p["id"] == PLUGIN for p in installed):
            res = claude(d, "plugin", "update", PLUGIN)
        else:
            res = claude(d, "plugin", "install", PLUGIN, "--scope", "user")
        out(f"{'✓' if res.returncode == 0 else '✗'} Claude plugin in {d}"
            + ("" if res.returncode == 0 else f": {res.stderr.strip()[:200]}"))


def uninstall_plugin(out):
    if not shutil.which("claude"):
        return
    for d in claude_dirs():
        claude(d, "plugin", "uninstall", PLUGIN)
    out("✓ Claude plugin removed")


def install(out=print):
    install_desktop(out)
    install_plugin(out)


def uninstall(out=print):
    uninstall_desktop(out)
    uninstall_plugin(out)
