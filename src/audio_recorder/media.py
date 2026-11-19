"""ffmpeg (shipped by imageio-ffmpeg on every platform) and desktop notifications."""

import re
import shutil
import subprocess
import sys

import imageio_ffmpeg

# No console window for the helpers spawned by the windowless tray on Windows
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class MediaError(Exception):
    pass


def ffmpeg_exe():
    return imageio_ffmpeg.get_ffmpeg_exe()


def ffmpeg(*args, capture=False):
    """Runs ffmpeg quietly; raises MediaError with its stderr on failure."""
    res = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-nostdin", "-y", *args],
        capture_output=True, creationflags=NO_WINDOW,
    )
    if res.returncode != 0:
        raise MediaError(res.stderr.decode(errors="replace").strip() or "ffmpeg failed")
    return res.stdout if capture else None


def probe(path):
    """{'duration': seconds, 'comment': str} read from ffmpeg's input banner."""
    res = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(path)],
        capture_output=True, creationflags=NO_WINDOW,
    )
    banner = res.stderr.decode(errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", banner)
    if not m:
        raise MediaError(f"not an audio file: {path}")
    duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    c = re.search(r"^\s+comment\s*:\s*(.*)$", banner, re.M)
    return {"duration": duration, "comment": c.group(1).strip() if c else "",
            "video": bool(re.search(r"Stream #.*: Video:", banner))}


def notify(title, message):
    """Best-effort desktop notification (the tray relays warnings on Windows)."""
    if sys.platform == "darwin":
        script = f"display notification {_applescript(message)} with title {_applescript(title)}"
        subprocess.run(["osascript", "-e", script], capture_output=True)
    elif sys.platform.startswith("linux") and shutil.which("notify-send"):
        subprocess.run(["notify-send", "-i", "audio-input-microphone", title, message],
                       capture_output=True)


def _applescript(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
