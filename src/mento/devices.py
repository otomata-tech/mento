"""Audio devices: the default microphone and the system audio (loopback).

soundcard captures the system audio natively on Linux (the PulseAudio or
PipeWire monitor of the default sink) and Windows (WASAPI loopback of the
default speaker). macOS has no loopback: a virtual device (BlackHole,
Background Music) has to be installed, else only the mic is recorded.
"""

import re
import shutil
import subprocess
import sys

VIRTUAL_LOOPBACK = re.compile(r"blackhole|background music|soundflower|loopback", re.I)


class DeviceError(Exception):
    pass


def pick():
    """(mic, system) soundcard devices; system is None when unavailable."""
    if len(sys.argv) < 2:
        sys.argv.append("mento")  # soundcard names its PulseAudio client from argv
    import soundcard as sc

    try:
        mic = sc.default_microphone()
        speaker = sc.default_speaker()
    except Exception as e:  # noqa: BLE001 — backend errors vary by platform
        raise DeviceError(f"no audio device: {e}") from e
    if mic is None:
        raise DeviceError("no microphone found")

    system = None
    if speaker is not None:
        system = next(
            (m for m in sc.all_microphones(include_loopback=True)
             if m.isloopback and m.id in (speaker.id, f"{speaker.id}.monitor")),
            None,
        )
    if system is None:
        system = next((m for m in sc.all_microphones()
                       if VIRTUAL_LOOPBACK.search(m.name) and m.id != mic.id), None)
    return mic, system


def unmute_mic():
    """Linux: unmutes the default source if muted. Returns True when it did."""
    if not sys.platform.startswith("linux") or not shutil.which("pactl"):
        return False
    out = subprocess.run(["pactl", "get-source-mute", "@DEFAULT_SOURCE@"],
                         capture_output=True, text=True).stdout
    if out.split()[-1:] in (["yes"], ["oui"]):
        subprocess.run(["pactl", "set-source-mute", "@DEFAULT_SOURCE@", "0"], capture_output=True)
        return True
    return False
