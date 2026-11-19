# audio-recorder

Record meetings and transcribe them with speaker diarization, on Linux, macOS and
Windows — from the terminal, a panel icon, or a Claude Code session that knows the
meeting's context.

```bash
audio-recorder start            # mic on the left channel, system audio on the right
audio-recorder pause / resume
audio-recorder stop
audio-recorder transcribe last --prompt "Acme, DSN, Marie Dupont" --speakers 3
audio-recorder search "budget" -C 2
```

## Install

```bash
uv tool install git+https://github.com/AlexisLaporte/audio-recorder
audio-recorder install   # panel icon at login, app menu entry, Claude Code plugin
audio-recorder setup     # speaker name, tokens, transcription engine
```

ffmpeg comes with the package. Per platform:

- **Linux**: PulseAudio or PipeWire. The panel icon uses AppIndicator from the system
  Python (`gir1.2-ayatanaappindicator3-0.1`, built in on Ubuntu).
- **Windows**: the system audio is captured natively (WASAPI loopback).
- **macOS**: there is no system audio capture without a virtual device — install
  [BlackHole](https://github.com/ExistentialAudio/BlackHole) or Background Music;
  otherwise only the mic is recorded.

## Transcription

Two engines, chosen with `--engine` or `TRANSCRIBE_ENGINE`:

- **WhisperX** (default, local): install it apart, it brings PyTorch and pins its own
  Python — `uv tool install whisperx --python 3.12`. Diarization needs a HuggingFace
  token (`HF_TOKEN`) and the conditions of `pyannote/speaker-diarization-community-1`
  accepted. Fast with an NVIDIA GPU; `WHISPER_DEVICE=cpu` otherwise (slow).
- **Voxtral** (Mistral API, `MISTRAL_API_KEY`): no GPU needed; the audio is sent to Mistral.

On dual-track recordings, the local speaker (`LOCAL_SPEAKER`) is pinned from the mic
track; diarization only tells the remote speakers apart.

## Commands

`rec` is a recording id (folder name), a unique prefix or part of it, or `last`.
Every read command takes `--json`; every command takes `--help`.

| Command | Description |
|---------|-------------|
| `start` / `pause` / `resume` / `stop [--trim]` | Record in the background (pauses excluded) |
| `status [--json]` | State and duration |
| `tray` | Panel icon: start, pause, stop, recent recordings |
| `transcribe [rec\|file]` | `--engine`, `--prompt`, `--speakers N\|MIN-MAX`, `--language`, `--model` |
| `list [--since D] [--limit N]` | All recordings, newest first |
| `show [rec]` | Duration, transcription settings, talk time per speaker |
| `cat [rec] [--speaker S] [--from T] [--to T]` | Print a transcript |
| `search TEXT [--since D] [--speaker S] [-C N]` | Search every transcript (case and accents ignored) |
| `path` / `open` / `rename rec NAME` | Folder path, file manager, rename to `<date>_NAME` |
| `trim [rec]` | Detect the end of the conversation and cut |
| `push [rec] [--with-audio]` | Store on [mento](https://mento.cc) for sharing and agent access |
| `setup` / `install [--uninstall]` | Settings; desktop integration and Claude plugin |

## Output

```
~/Recordings/
└── 20261118_point_client/
    ├── audio.mp3          # stereo: mic left, system audio right
    ├── transcript.txt     # [mm:ss] SPEAKER: text
    └── meta.json          # start, duration, transcription settings
```

## Claude Code plugin

The repo is a Claude Code plugin (and its own marketplace) shipping the
`audio-recorder` skill: a session finds the meeting, transcribes it with the mission's
vocabulary and participant count, names the speakers, then writes the minutes where
the project lives. `audio-recorder install` enables it in every Claude Code config
dir; by hand:

```
/plugin install audio-recorder --marketplace AlexisLaporte/audio-recorder
```

## Development

```bash
uv sync
uv run pytest -q
uv tool install -e .   # the installed command follows the checkout
```

## License

MIT
