"""Live transcription: live.txt grows in the recording folder while it records.

A process detached from the recorder follows mic.pcm and system.pcm as they
grow, cuts them at a quiet instant every ~20 s and transcribes each piece, with
the engine of TRANSCRIBE_ENGINE unless told otherwise:
- whisperx: a faster-whisper model loaded once for the whole meeting, in a
  worker run under the WhisperX tool's Python (whisper_worker.py) — the
  whisperx command itself reloads its models on every call, far too slow;
- voxtral: the Mistral API, for a machine without WhisperX.
Lines carry the recording's clock, pauses excluded, like the final transcript.

Speakers: the mic track tells the local speaker apart from the others, using
the levels of the whole recording so far (see transcript.classify). Remote
speakers are not told apart live — diarization labels change from one piece
to the next — so they are OTHERS, and UNKNOWN while no remote side played.
The diarized transcript.txt is still made after `stop` by `mento transcribe`.

The process keeps its progress in live.json (heartbeat, offset, last error),
so `mento live` can tell it is alive and a new one resumes where it stopped.
It quits when the recording stops: the last seconds are left to the final
transcript.
Usage: python -m mento.live <folder> [--engine E] [--model M] [--prompt P] [--language L]
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import wave
from pathlib import Path

import httpx
import numpy as np

from . import config, transcribe
from .recorder import SAMPLE_RATE
from .session import Session
from .transcript import classify, fmt_stamp, is_local

FRAME = SAMPLE_RATE // 10  # 100 ms, the frame of transcript.classify
CUT_AFTER_S = 20  # a piece is sent once this much audio is pending…
MIN_PIECE_S = 10  # …cut at the quietest frame between these two marks
SILENT_DB = -60.0  # a piece without any frame above this is not sent
POLL_S = 1.0
RETRY_S = 15
HEARTBEAT_TIMEOUT = 120  # longer than one Voxtral call
REQUEST_TIMEOUT = 60
OTHERS = "OTHERS"
ACTIVE = ("starting", "recording", "paused")


class LiveError(Exception):
    pass


def check_ready(cfg, engine):
    """Fails before anything starts when the engine cannot run."""
    if engine == "voxtral":
        if not cfg.get("MISTRAL_API_KEY"):
            raise LiveError("live with Voxtral: MISTRAL_API_KEY not set, run 'mento setup'")
    else:
        transcribe.whisperx_python()


def live_file(folder):
    return Path(folder) / "live.txt"


def read_state(folder):
    try:
        return json.loads((Path(folder) / "live.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def write_state(folder, **state):
    state["heartbeat"] = time.time()
    path = Path(folder) / "live.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, path)


def mark_starting(folder):
    """Claims the folder before the process is up, so a second `mento live` does not spawn another."""
    write_state(folder, **{**(read_state(folder) or {}), "status": "starting"})


def is_running(folder):
    st = read_state(folder)
    return bool(st and st.get("status") in ("starting", "running")
                and time.time() - st["heartbeat"] < HEARTBEAT_TIMEOUT)


def frame_levels(samples):
    """dB of each full 100 ms frame of int16 samples."""
    frames = samples[: len(samples) // FRAME * FRAME].astype(np.float64).reshape(-1, FRAME) / 32768
    power = (frames ** 2).mean(axis=1)
    return [float(10 * np.log10(p)) if p else -120.0 for p in power]


def cut_point(levels):
    """Frames to send: up to the quietest frame between MIN_PIECE_S and CUT_AFTER_S."""
    lo, hi = MIN_PIECE_S * 10, min(len(levels), CUT_AFTER_S * 10)
    return lo + int(np.argmin(levels[lo:hi])) + 1


def write_wav(path, tracks):
    """48 kHz WAV, one channel per track (mic left, system right)."""
    data = np.stack(tracks, axis=1) if len(tracks) > 1 else tracks[0]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(len(tracks))
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(data.astype("<i2").tobytes())


def engine_and_model(cfg, engine=None, model=None):
    engine = engine or cfg.get("TRANSCRIBE_ENGINE")
    if engine not in transcribe.ENGINES:
        raise LiveError(f"unknown engine: {engine} (whisperx or voxtral)")
    default = cfg.get("VOXTRAL_MODEL") if engine == "voxtral" else cfg.get("WHISPER_MODEL")
    return engine, model or default


class LocalWhisper:
    """A runner backed by whisper_worker.py, whose model stays loaded for the meeting."""

    def __init__(self, cfg, model):
        self.cmd = [transcribe.whisperx_python(), str(Path(__file__).with_name("whisper_worker.py")),
                    model, cfg.get("WHISPER_DEVICE"), cfg.get("WHISPER_COMPUTE_TYPE")]
        self.proc = None
        self.ready = False

    def start(self):
        """Starts loading the model, while the first piece is still being recorded."""
        if self.proc is None:
            # stderr (model loading, warnings) goes to this process's own log, live.log
            self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         text=True, encoding="utf-8")
            self.ready = False

    def ask(self, request=None):
        if request is not None:
            self.proc.stdin.write(json.dumps(request) + "\n")
            self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            self.close()
            raise LiveError("the whisper worker died, see live.log")
        reply = json.loads(line)
        if "error" in reply:
            raise LiveError(f"whisper worker: {reply['error']}")
        return reply

    def __call__(self, wav, cfg, *, language, vocab, **_):
        self.start()
        if not self.ready:
            self.ask()  # {"ready": true} once the model is loaded
            self.ready = True
        return self.ask({"wav": str(wav), "language": language, "prompt": vocab})["segments"]

    def close(self):
        if self.proc is not None:
            self.proc.kill()
            self.proc.wait()
            self.proc = None


class Live:
    def __init__(self, folder, cfg, *, engine=None, model=None, prompt="", language=None,
                 runner=None, session=None):
        self.folder = Path(folder)
        self.cfg = cfg
        self.engine, self.model = engine_and_model(cfg, engine, model)
        self.vocab = transcribe.vocabulary(cfg, prompt)
        self.language = cfg.get("WHISPER_LANGUAGE") if language is None else language
        if runner is None:
            runner = self.voxtral if self.engine == "voxtral" else LocalWhisper(cfg, self.model)
        self.runner = runner
        self.session = session or Session(config.state_dir())
        self.local_speaker = cfg.get("LOCAL_SPEAKER")
        self.mic_db, self.sys_db = [], []
        st = read_state(self.folder) or {}
        self.offset = st.get("offset", 0)  # samples already transcribed
        self.error = None

    def voxtral(self, wav, cfg, **kw):
        # A short timeout: a stuck call would stall the live transcript, the piece is retried
        client = httpx.Client(timeout=REQUEST_TIMEOUT)
        with client:
            return transcribe.run_voxtral(wav, cfg, client=client, **kw)

    # ── Tracks ───────────────────────────────────────────────────────────────

    def track_paths(self):
        paths = [self.folder / "mic.pcm", self.folder / "system.pcm"]
        return [p for p in paths if p.exists()]

    def available(self, paths):
        """Samples present on every track (the system track can lag a block)."""
        return min(p.stat().st_size for p in paths) // 2

    def read(self, paths, start, end):
        tracks = []
        for p in paths:
            with open(p, "rb") as f:  # closed at once: the recorder deletes them at stop
                f.seek(start * 2)
                tracks.append(np.frombuffer(f.read((end - start) * 2), "<i2"))
        return tracks

    def catch_up_levels(self, paths):
        """Levels of the audio transcribed by a previous process, for speaker attribution."""
        if self.offset and not self.mic_db:
            tracks = self.read(paths, 0, self.offset)
            self.mic_db = frame_levels(tracks[0])
            self.sys_db = frame_levels(tracks[1]) if len(tracks) > 1 else []

    # ── One piece ────────────────────────────────────────────────────────────

    def next_piece(self):
        """(paths, end sample) of the next piece to send, None while too little is pending."""
        paths = self.track_paths()
        if not paths:
            return None
        self.catch_up_levels(paths)
        pending = self.available(paths) - self.offset
        if pending < CUT_AFTER_S * SAMPLE_RATE:
            return None
        window = self.read(paths, self.offset, self.offset + CUT_AFTER_S * SAMPLE_RATE)
        levels = np.maximum.reduce([frame_levels(t) for t in window])
        return paths, self.offset + cut_point(list(levels)) * FRAME

    def transcribe_piece(self, paths, end):
        tracks = self.read(paths, self.offset, end)
        mic_db = frame_levels(tracks[0])
        sys_db = frame_levels(tracks[1]) if len(tracks) > 1 else []
        lines = []
        if max(mic_db + sys_db) >= SILENT_DB:
            with tempfile.TemporaryDirectory() as tmp:
                piece = Path(tmp) / "piece.wav"
                write_wav(piece, tracks)
                wav = transcribe.prepare_input(piece, len(tracks) > 1, tmp)
                segments = self.runner(wav, self.cfg, model=self.model, language=self.language,
                                       vocab=self.vocab, speakers=None,
                                       log_path=self.folder / "voxtral-live.log", out=lambda _: None)
            lines = self.lines(segments, mic_db, sys_db)
        # Levels are kept only once the piece is done: a retry must not count them twice
        self.mic_db += mic_db
        self.sys_db += sys_db
        if lines:
            with open(live_file(self.folder), "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        self.offset = end

    def lines(self, segments, mic_db, sys_db):
        start_s = self.offset / SAMPLE_RATE
        frames = classify(self.mic_db + mic_db, self.sys_db + sys_db) if sys_db else None
        lines = []
        for seg in segments:
            text = seg.get("text", "").strip()
            if not text:
                continue
            start, end = start_s + seg.get("start", 0), start_s + seg.get("end", 0)
            if frames is None:
                speaker = "UNKNOWN"
            else:
                speaker = self.local_speaker if is_local(frames, start, end) else OTHERS
            lines.append(f"[{fmt_stamp(start)}] {speaker}: {text}")
        return lines

    # ── Loop ─────────────────────────────────────────────────────────────────

    def recording(self):
        st = self.session.read()
        return bool(st and Path(st["folder"]) == self.folder and self.session.status() in ACTIVE)

    def write_state(self, status):
        state = {"status": status, "pid": os.getpid(), "offset": self.offset,
                 "transcribed_s": round(self.offset / SAMPLE_RATE), "engine": self.engine,
                 "model": self.model, "prompt": self.vocab}
        if self.error:
            state["error"] = self.error
        write_state(self.folder, **state)

    def run(self):
        try:
            self.loop()
        finally:
            if isinstance(self.runner, LocalWhisper):
                self.runner.close()

    def loop(self):
        check_ready(self.cfg, self.engine)
        if isinstance(self.runner, LocalWhisper):
            self.runner.start()
        retry_at = 0.0
        while self.recording():
            self.write_state("running")
            piece = self.next_piece() if time.time() >= retry_at else None
            if piece is None:
                time.sleep(POLL_S)
                continue
            try:
                self.transcribe_piece(*piece)
                self.error = None
            except Exception as e:  # noqa: BLE001 — kept in live.json, the piece is retried
                self.error = f"{type(e).__name__}: {e}"
                retry_at = time.time() + RETRY_S
                traceback.print_exc()
        self.write_state("done")


def main():
    parser = argparse.ArgumentParser(prog="mento.live")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--engine")
    parser.add_argument("--model")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--language")
    args = parser.parse_args()
    live = Live(args.folder, config.Config.load(), engine=args.engine, model=args.model,
                prompt=args.prompt, language=args.language)
    try:
        live.run()
    except Exception as e:  # noqa: BLE001 — reported through live.json
        live.error = f"{type(e).__name__}: {e}"
        live.write_state("failed")
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
