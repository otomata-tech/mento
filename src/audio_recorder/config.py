"""Configuration and per-user locations.

The config file keeps the shell-compatible KEY="value" format of the first
versions (~/.config/audio-recorder/config on Linux, the platform's config dir
elsewhere). Environment variables override it.
"""

import os
import re
from pathlib import Path

import platformdirs

APP = "audio-recorder"

# Recordings tagged with this keep the mic on the left channel and the system
# audio on the right, which transcript.py uses to pin the local speaker.
DUAL_TRACK_TAG = "audio-recorder:L=mic,R=system"

KEYS = {
    "RECORDINGS_DIR": "Recordings directory",
    "LOCAL_SPEAKER": "Your name in transcripts",
    "TRANSCRIBE_ENGINE": "Transcription engine (whisperx/voxtral)",
    "WHISPER_MODEL": "Whisper model (large-v3, medium, small…)",
    "WHISPER_LANGUAGE": "Transcription language, empty = auto (fr/en/…)",
    "WHISPER_INITIAL_PROMPT": "Vocabulary always primed (names, products, acronyms)",
    "HF_TOKEN": "HuggingFace token (WhisperX diarization)",
    "MISTRAL_API_KEY": "Mistral API key (Voxtral)",
    "MENTO_API_TOKEN": "mento.cc API token (push), created on mento.cc",
}
SECRET_KEYS = {"HF_TOKEN", "MISTRAL_API_KEY", "MENTO_API_TOKEN"}
# Tuning knobs read from the environment or the file, not asked by setup
EXTRA_KEYS = {"WHISPER_DEVICE", "WHISPER_BATCH_SIZE", "WHISPER_COMPUTE_TYPE", "VOXTRAL_MODEL",
              "MENTO_API_URL"}

DEFAULTS = {
    "RECORDINGS_DIR": str(Path.home() / "Recordings"),
    "LOCAL_SPEAKER": "ME",
    "TRANSCRIBE_ENGINE": "whisperx",
    "WHISPER_MODEL": "large-v3",
    "WHISPER_DEVICE": "cuda",
    "WHISPER_BATCH_SIZE": "4",
    "WHISPER_COMPUTE_TYPE": "int8",
    "VOXTRAL_MODEL": "voxtral-mini-latest",
    "MENTO_API_URL": "https://mento.cc/api/v1",
}


def config_dir():
    return Path(platformdirs.user_config_dir(APP, appauthor=False, roaming=True))


def config_file():
    return config_dir() / "config"


def state_dir():
    """Private per-user dir for the recording session (never a shared /tmp)."""
    path = Path(platformdirs.user_runtime_dir(APP, appauthor=False))
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def parse(text):
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = re.sub(r'\\(["\\])', r"\1", value[1:-1])
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def dump(values):
    lines = []
    for key in [*KEYS, *sorted(EXTRA_KEYS)]:
        if values.get(key):
            escaped = values[key].replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key}="{escaped}"')
    return "\n".join(lines) + "\n"


class Config:
    def __init__(self, values):
        self.values = values

    @classmethod
    def load(cls):
        path = config_file()
        values = dict(DEFAULTS)
        if path.exists():
            values.update({k: v for k, v in parse(path.read_text(encoding="utf-8")).items() if v})
        for key in [*KEYS, *EXTRA_KEYS]:
            if os.environ.get(key):
                values[key] = os.environ[key]
        return cls(values)

    def get(self, key):
        return self.values.get(key, "")

    @property
    def recordings_dir(self):
        path = Path(os.path.expanduser(self.get("RECORDINGS_DIR")))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save(self):
        path = config_file()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        stored = {k: v for k, v in self.values.items() if v and DEFAULTS.get(k) != v}
        # Created private (0600) then swapped in: the tokens are never readable by others
        tmp = path.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(dump(stored))
        os.replace(tmp, path)
        return path
