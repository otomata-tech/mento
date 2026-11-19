"""Trim: find where the conversation ended and cut the trailing audio.

One ffmpeg pass detects every silence over 3 s. The cut is the first silence
of 10 s or more after the first 5 minutes, if silences get denser after it
(or it lasts 15 s); otherwise the start of the first 5-minute window whose
silence count jumps past 1.5x the running average.
"""

import re
import shutil
import subprocess
from pathlib import Path

from . import media
from .library import set_meta

WINDOW = 300  # seconds
MIN_DURATION = 600


class TrimError(Exception):
    pass


def detect_silences(audio):
    """[(start, duration)] of silences over 3 s below -30 dB."""
    res = subprocess.run(
        [media.ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(audio),
         "-af", "silencedetect=noise=-30dB:d=3", "-f", "null", "-"],
        capture_output=True, creationflags=media.NO_WINDOW,
    )
    log = res.stderr.decode(errors="replace")
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", log)]
    durations = [float(x) for x in re.findall(r"silence_duration: ([0-9.]+)", log)]
    return list(zip(starts, durations))


def find_cut(silences, duration):
    """Cut point in seconds, or None when no clear end is found."""
    starts = [s for s, _ in silences]

    for start, length in silences:
        if start < WINDOW or length < 10:
            continue
        before = sum(1 for s in starts if start - WINDOW <= s < start)
        after = sum(1 for s in starts if start < s <= start + WINDOW)
        if after >= before or length >= 15:
            return start

    counts = [sum(1 for s in starts if t <= s < t + WINDOW) for t in range(0, int(duration), WINDOW)]
    running = 0
    for idx, count in enumerate(counts):
        if idx >= 2:
            avg = running // idx
            if avg > 0 and count > avg * 3 / 2:
                window_start = idx * WINDOW
                for start, length in silences:
                    if start >= window_start and length >= 5:
                        return start
        running += count
    return None


def trim(folder, confirm=None, out=print):
    """Cuts folder/audio.mp3, keeping the original as audio_full.mp3."""
    folder = Path(folder)
    audio, full = folder / "audio.mp3", folder / "audio_full.mp3"
    if not audio.exists():
        raise TrimError(f"no audio: {audio}")
    if full.exists():
        raise TrimError("already trimmed (audio_full.mp3 exists)")
    info = media.probe(audio)
    if info["duration"] < MIN_DURATION:
        raise TrimError(f"recording too short ({info['duration']:.0f} s) — nothing to trim")

    out("Analyzing silences…")
    cut = find_cut(detect_silences(audio), info["duration"])
    if cut is None:
        raise TrimError("no clear end of conversation detected")
    out(f"Conversation end at {cut / 60:.1f} min (cuts {(info['duration'] - cut) / 60:.1f} min)")
    if confirm and not confirm():
        return None

    shutil.copy2(audio, full)
    media.ffmpeg("-i", str(full), "-t", f"{cut:.3f}", "-c", "copy",
                 "-metadata", f"comment={info['comment']}", str(audio))
    set_meta(folder, {"duration_s": round(cut)})
    return cut
