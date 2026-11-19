"""Transcript lines from diarized segments, fixing the local speaker.

Dual-track recordings (see DUAL_TRACK_TAG in config.py) carry the mic on the
left channel and the system audio on the right. For those, segments where
the mic is active — beyond what the speakers leak into it — are attributed
to the local speaker, overriding the diarization, which only guesses from the
mixed voice and often mislabels short replies. Remote speakers keep their
labels.
"""

import array
import math
import sys

from .media import ffmpeg

SAMPLE_RATE = 4000  # energy only: the speech band below 2 kHz is enough
FRAME = SAMPLE_RATE // 10  # 100 ms
ACTIVE_DB = 20.0  # a frame is active this far above its channel's noise floor
BLEED_MARGIN_DB = 10.0  # local speech must beat the speaker leak by this much
MIN_REMOTE_ACTIVITY = 0.05  # below this, nothing plays remotely: in-room meeting


def load_channels(audio):
    raw = ffmpeg("-i", str(audio), "-ac", "2", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-",
                 capture=True)
    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples[0::2], samples[1::2]


def frame_db(channel):
    out = []
    for i in range(0, len(channel) - FRAME + 1, FRAME):
        power = sum(s * s for s in channel[i:i + FRAME]) / FRAME
        out.append(10 * math.log10(power / 32768**2) if power else -120.0)
    return out


def activity(dbs):
    floor = sorted(dbs)[len(dbs) // 10]
    return [d >= floor + ACTIVE_DB for d in dbs]


def local_frames(mic, system):
    """Per frame: (local speech, any speech). None when no remote side played."""
    mic_db, sys_db = frame_db(mic), frame_db(system)
    if not mic_db:
        return None
    mic_on, sys_on = activity(mic_db), activity(sys_db)
    if sum(sys_on) < MIN_REMOTE_ACTIVITY * len(sys_on):
        return None
    # Typical mic/system gap while the system plays = how much the speakers
    # leak into the mic (very negative with a headset).
    gaps = sorted(m - s for m, s, on in zip(mic_db, sys_db, sys_on) if on)
    bleed = gaps[len(gaps) // 2]
    local = [on and m - s >= bleed + BLEED_MARGIN_DB
             for m, s, on in zip(mic_db, sys_db, mic_on)]
    active = [a or b for a, b in zip(mic_on, sys_on)]
    return local, active


def is_local(frames, start, end):
    local, active = frames
    span = range(int(start * 10), min(int(end * 10) + 1, len(local)))
    n_active = sum(active[i] for i in span)
    return n_active > 0 and sum(local[i] for i in span) >= n_active / 2


def fmt_stamp(seconds):
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_lines(segments, audio, dual_track, local_speaker):
    """[(mm:ss) SPEAKER: text] lines; segments are {start, end, speaker, text}."""
    frames = local_frames(*load_channels(audio)) if dual_track else None
    lines = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        start = seg.get("start", 0)
        speaker = seg.get("speaker") or "UNKNOWN"
        if frames and is_local(frames, start, seg.get("end", start)):
            speaker = local_speaker
        lines.append(f"[{fmt_stamp(start)}] {speaker}: {text}")
    return lines
