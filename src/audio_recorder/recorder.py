"""The recorder process: captures the mic and the system audio in the background.

Each device is captured by its own thread into a raw 16-bit mono file in the
recording folder (mic.pcm, system.pcm), which survives a crash. Pause closes
the devices (the mic indicator goes off) and pads the shorter track so both
stay aligned. Stop encodes them into one stereo audio.mp3: mic on the left,
system audio on the right, tagged with DUAL_TRACK_TAG.

Usage: python -m audio_recorder.recorder <folder>
"""

import math
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np

from . import config, devices, media
from .library import set_meta
from .session import Session

SAMPLE_RATE = 48000
BLOCK = SAMPLE_RATE // 10  # 100 ms
SILENT_DB = -60.0
WATCHDOG_S = 120
TRACKS = ("mic", "system")


class Track(threading.Thread):
    """One device captured into <folder>/<name>.pcm while `running` is set."""

    def __init__(self, name, device, folder, running, stopped):
        super().__init__(name=f"track-{name}", daemon=True)
        self.device = device
        self.path = Path(folder) / f"{name}.pcm"
        self.running = running
        self.stopped = stopped
        self.opened = threading.Event()  # first capture block received
        self.closed = threading.Event()  # device released (paused or stopped)
        self.closed.set()
        self.peak_db = -120.0
        self.error = None

    def run(self):
        try:
            with open(self.path, "ab") as out:
                while not self.stopped.is_set():
                    if not self.running.wait(timeout=0.1):
                        continue
                    self.closed.clear()
                    try:
                        # An explicit blocksize: without it soundcard drops a third of the frames
                        with self.device.recorder(samplerate=SAMPLE_RATE, channels=1,
                                                  blocksize=BLOCK) as rec:
                            while self.running.is_set() and not self.stopped.is_set():
                                block = np.clip(rec.record(numframes=BLOCK)[:, 0], -1.0, 1.0)
                                out.write((block * 32767).astype("<i2").tobytes())
                                self.opened.set()
                                rms = float(np.sqrt(np.mean(block ** 2))) if len(block) else 0.0
                                self.peak_db = max(self.peak_db, 20 * math.log10(rms) if rms else -120.0)
                    finally:
                        out.flush()
                        self.closed.set()
        except Exception as e:  # noqa: BLE001 — reported through the session
            self.error = f"{self.name}: {e}"
            self.closed.set()


def pad_tracks(folder):
    """Pads the shorter track with silence so both end at the same instant."""
    paths = [Path(folder) / f"{t}.pcm" for t in TRACKS]
    paths = [p for p in paths if p.exists()]
    longest = max((p.stat().st_size for p in paths), default=0)
    for p in paths:
        missing = longest - p.stat().st_size
        if missing:
            with open(p, "ab") as f:
                f.write(b"\0" * missing)


def finalize(folder):
    """Encodes mic.pcm (+ system.pcm) into audio.mp3; returns its duration."""
    folder = Path(folder)
    mic, system = folder / "mic.pcm", folder / "system.pcm"
    if not mic.exists() or mic.stat().st_size == 0:
        raise media.MediaError(f"nothing recorded in {folder}")
    pad_tracks(folder)
    raw = ["-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i"]
    encode = ["-c:a", "libmp3lame", "-q:a", "2"]
    target = folder / "audio.mp3"
    if system.exists():
        media.ffmpeg(*raw, str(mic), *raw, str(system), "-filter_complex",
                     "[0:a][1:a]join=inputs=2:channel_layout=stereo:map=0.0-FL|1.0-FR[out]",
                     "-map", "[out]", *encode, "-metadata", f"comment={config.DUAL_TRACK_TAG}",
                     str(target))
    else:
        media.ffmpeg(*raw, str(mic), *encode, str(target))
    duration = round(mic.stat().st_size / 2 / SAMPLE_RATE)
    mic.unlink()
    system.unlink(missing_ok=True)
    set_meta(folder, {"duration_s": duration})
    return duration


def run(folder, session, mic, system):
    """Records until a "stop" command; drives the session state meanwhile."""
    running, stopped = threading.Event(), threading.Event()
    tracks = [Track("mic", mic, folder, running, stopped)]
    if system is not None:
        tracks.append(Track("system", system, folder, running, stopped))

    # "starting" until every device delivered audio, so `start` reports a dead device
    state = {"status": "starting", "folder": str(folder), "recorded_s": 0.0,
             "segment_start": None, "system_audio": system is not None}
    running.set()
    for t in tracks:
        t.start()

    last_check = time.time()
    while True:
        failed = next((t.error for t in tracks if t.error), None)
        if failed:
            stopped.set()
            session.write(**{**state, "status": "failed", "error": failed})
            return 1

        if state["status"] == "starting" and all(t.opened.is_set() for t in tracks):
            state.update(status="recording", segment_start=time.time())

        command = session.take_command() if state["status"] != "starting" else None
        if command == "pause" and state["status"] == "recording":
            running.clear()
            for t in tracks:
                t.closed.wait(timeout=5)
            pad_tracks(folder)
            state["recorded_s"] += time.time() - state["segment_start"]
            state.update(status="paused", segment_start=None)
        elif command == "resume" and state["status"] == "paused":
            state.update(status="recording", segment_start=time.time())
            running.set()
        elif command == "stop":
            break

        now = time.time()
        if state["status"] == "recording" and now - last_check >= WATCHDOG_S:
            peak = max(t.peak_db for t in tracks)
            if peak < SILENT_DB:
                state["warning"] = f"Audio may be empty ({peak:.0f} dB) — check your audio source"
                media.notify("⚠️ audio-recorder", state["warning"])
            else:
                state.pop("warning", None)
            for t in tracks:
                t.peak_db = -120.0
            last_check = now
        session.write(**state)
        time.sleep(0.2)

    running.clear()
    stopped.set()
    for t in tracks:
        t.join(timeout=5)
    if state["status"] == "recording":
        state["recorded_s"] += time.time() - state["segment_start"]
    if not any(t.opened.is_set() for t in tracks):
        session.write(**{**state, "status": "failed", "error": "stopped before any audio was captured"})
        return 1
    state.update(status="stopping", segment_start=None)

    # Encode in a thread so the heartbeat keeps the session alive meanwhile
    result = {}

    def encode():
        try:
            result["duration"] = finalize(folder)
        except Exception as e:  # noqa: BLE001
            result["error"] = str(e)

    worker = threading.Thread(target=encode)
    worker.start()
    while worker.is_alive():
        session.write(**state)
        worker.join(timeout=0.5)
    if "error" in result:
        session.write(**{**state, "status": "failed", "error": result["error"]})
        return 1
    session.clear()
    return 0


def main():
    folder = Path(sys.argv[1])
    session = Session(config.state_dir())
    try:
        devices.unmute_mic()
        mic, system = devices.pick()
        return run(folder, session, mic, system)
    except Exception as e:  # noqa: BLE001 — the CLI reads the failure from the session
        traceback.print_exc()
        session.write(status="failed", folder=str(folder), error=f"{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
