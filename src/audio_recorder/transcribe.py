"""Transcription with speaker diarization: WhisperX (local) or Voxtral (Mistral API).

Both engines get the same input — a 16 kHz mono downmix in which, for
dual-track recordings, each channel was levelled on its own (the system
channel follows the output volume and can sit 20 dB below the mic) — and
return segments {start, end, speaker, text}. transcript.py then pins the
local speaker from the mic track and writes the lines.

WhisperX is a separate tool (`uv tool install whisperx`): it brings PyTorch
and pins its own Python, so it is called as a command, never imported.
"""

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

from . import config, media
from .library import set_meta
from .transcript import format_lines

ENGINES = ("whisperx", "voxtral")
MISTRAL_URL = "https://api.mistral.ai/v1/audio/transcriptions"
PROMPT_WARN_CHARS = 600  # Whisper keeps only the last ~220 tokens of its prompt


class TranscribeError(Exception):
    pass


def parse_speakers(value):
    """'3' → (3, 3), '2-4' → (2, 4), '' → None."""
    if not value:
        return None
    m = re.fullmatch(r"(\d+)(?:-(\d+))?", value)
    if not m:
        raise TranscribeError(f"invalid speaker count: {value} (expected N or MIN-MAX)")
    low = int(m.group(1))
    high = int(m.group(2) or low)
    if low > high:
        raise TranscribeError(f"invalid speaker range: {value}")
    return low, high


def vocabulary(cfg, prompt):
    """Global vocabulary first: Whisper drops the start of an over-long prompt."""
    return ", ".join(p for p in (cfg.get("WHISPER_INITIAL_PROMPT"), prompt) if p)


def prepare_input(audio, dual_track, workdir):
    """16 kHz mono WAV, each channel levelled first on dual-track recordings."""
    wav = Path(workdir) / "input.wav"
    levelling = ["-af", "dynaudnorm=n=0,pan=mono|c0=0.5*c0+0.5*c1"] if dual_track else ["-ac", "1"]
    media.ffmpeg("-i", str(audio), *levelling, "-ar", "16000", str(wav))
    return wav


# ── Engines ──────────────────────────────────────────────────────────────────

def run_whisperx(wav, cfg, *, model, language, vocab, speakers, log_path, out=print):
    exe = shutil.which("whisperx")
    if not exe:
        raise TranscribeError(
            "whisperx not found. Install it: uv tool install whisperx --python 3.12 "
            "(or use --engine voxtral)")
    if not cfg.get("HF_TOKEN"):
        raise TranscribeError("HF_TOKEN not set (diarization): run 'audio-recorder setup'")
    cmd = [exe, str(wav), "--model", model, "--device", cfg.get("WHISPER_DEVICE"),
           "--batch_size", cfg.get("WHISPER_BATCH_SIZE"),
           "--compute_type", cfg.get("WHISPER_COMPUTE_TYPE"),
           "--diarize",
           "--output_format", "json", "--output_dir", str(wav.parent)]
    if language:
        cmd += ["--language", language]
    if vocab:
        cmd += ["--initial_prompt", vocab]
    if speakers:
        cmd += ["--min_speakers", str(speakers[0]), "--max_speakers", str(speakers[1])]

    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                # The token goes through the environment (huggingface_hub reads
                                # HF_TOKEN), never argv, which other users can list
                                env={**os.environ, "PYTHONWARNINGS": "ignore",
                                     "HF_TOKEN": cfg.get("HF_TOKEN")})
        for line in proc.stdout:
            log.write(line)
            if line.startswith("Transcript:"):
                out(line.split("]", 1)[-1].strip())
        if proc.wait() != 0:
            raise TranscribeError(f"WhisperX failed, see {log_path}")
    result = json.loads((wav.parent / "input.json").read_text(encoding="utf-8"))
    return [{"start": s.get("start", 0), "end": s.get("end", 0),
             "speaker": s.get("speaker"), "text": s.get("text", "")}
            for s in result.get("segments", [])]


def run_voxtral(wav, cfg, *, model, language, vocab, speakers, log_path, out=print, client=None):
    key = cfg.get("MISTRAL_API_KEY")
    if not key:
        raise TranscribeError("MISTRAL_API_KEY not set: run 'audio-recorder setup'")
    if speakers:
        out("Voxtral does not take a speaker count — ignored")
    upload = wav.with_suffix(".mp3")
    media.ffmpeg("-i", str(wav), "-c:a", "libmp3lame", "-b:a", "64k", str(upload))

    data = {"model": model, "diarize": "true", "timestamp_granularities": "segment"}
    if language:
        data["language"] = language
    terms = [term.strip() for term in vocab.split(",") if term.strip()]
    if terms:
        data["context_bias"] = terms  # one form field per term

    client = client or httpx.Client(timeout=httpx.Timeout(30, read=1800))
    with open(upload, "rb") as f:
        resp = client.post(MISTRAL_URL, headers={"Authorization": f"Bearer {key}"},
                           data=data, files={"file": ("audio.mp3", f, "audio/mpeg")})
    Path(log_path).write_text(resp.text, encoding="utf-8")
    if resp.status_code != 200:
        raise TranscribeError(f"Voxtral failed (HTTP {resp.status_code}): {resp.text[:300]}")
    return voxtral_segments(resp.json())


def voxtral_segments(payload):
    """Voxtral segments, speakers renamed SPEAKER_00, SPEAKER_01… by first appearance."""
    names = {}
    segments = []
    for s in payload.get("segments", []):
        sid = s.get("speaker_id")
        if sid is not None and sid not in names:
            names[sid] = f"SPEAKER_{len(names):02d}"
        segments.append({"start": s.get("start", 0), "end": s.get("end", 0),
                         "speaker": names.get(sid), "text": s.get("text", "")})
    return segments


RUNNERS = {"whisperx": run_whisperx, "voxtral": run_voxtral}


# ── Pipeline ─────────────────────────────────────────────────────────────────

def transcribe(cfg, *, folder=None, file=None, engine=None, prompt="", speakers="",
               language=None, model=None, out=print):
    """Transcribes a recording folder or a media file; returns the transcript path."""
    engine = engine or cfg.get("TRANSCRIBE_ENGINE")
    if engine not in ENGINES:
        raise TranscribeError(f"unknown engine: {engine} (whisperx or voxtral)")
    span = parse_speakers(speakers)
    language = cfg.get("WHISPER_LANGUAGE") if language is None else language
    model = model or (cfg.get("VOXTRAL_MODEL") if engine == "voxtral" else cfg.get("WHISPER_MODEL"))
    vocab = vocabulary(cfg, prompt)
    if len(vocab) > PROMPT_WARN_CHARS:
        out(f"Vocabulary is {len(vocab)} chars — Whisper keeps only ~220 tokens, the start is dropped")

    if folder:
        audio, out_dir, transcript = folder / "audio.mp3", folder, folder / "transcript.txt"
    else:
        audio, out_dir = Path(file), Path(file).parent
        transcript = out_dir / f"{audio.stem}_transcript.txt"
    if not audio.exists():
        raise TranscribeError(f"no audio file: {audio}")

    info = media.probe(audio)
    if info["video"]:
        extracted = out_dir / f"{audio.stem}.mp3"
        out("Extracting audio from video…")
        media.ffmpeg("-i", str(audio), "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(extracted))
        audio, info = extracted, media.probe(extracted)
    dual_track = info["comment"] == config.DUAL_TRACK_TAG

    out(f"Transcribing with {engine} ({model}): {audio}")
    with tempfile.TemporaryDirectory() as tmp:
        wav = prepare_input(audio, dual_track, tmp)
        segments = RUNNERS[engine](wav, cfg, model=model, language=language, vocab=vocab,
                                   speakers=span, log_path=out_dir / f"{engine}.log", out=out)
        lines = format_lines(segments, audio, dual_track, cfg.get("LOCAL_SPEAKER"))
    if not lines:
        raise TranscribeError("empty transcript — the audio may be silent")
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if folder:
        set_meta(folder, {"transcription": {
            "transcribed_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "engine": engine, "model": model, "language": language or "",
            "speakers": speakers, "prompt": vocab, "dual_track": dual_track,
        }})
    return transcript

