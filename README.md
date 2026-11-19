# audio-recorder

Record meetings and transcribe them with speaker diarization — from the terminal,
a panel icon, or a Claude Code session that knows the meeting's context.

```bash
audio-recorder start            # mic on the left channel, system audio on the right
audio-recorder pause / resume
audio-recorder stop
audio-recorder transcribe last --prompt "Acme, DSN, Marie Dupont" --speakers 3
audio-recorder search "budget" -C 2
```

## Install

```bash
git clone https://github.com/AlexisLaporte/audio-recorder.git
cd audio-recorder && ./install.sh
audio-recorder setup    # Configure HuggingFace token
```

By default `install.sh` **copies** the tool into `~/.local/share/audio-recorder/` and links a
launcher in `~/.local/bin` — the install keeps working even if you move or delete this checkout.

```bash
./install.sh --link       # Dev mode: symlink to this checkout (edits go live)
./install.sh --uninstall  # Remove the install (keeps your config & recordings)
```

**Requirements**: ffmpeg, [whisperx](https://github.com/m-bain/whisperX), python3.
Panel icon (Linux): AppIndicator (`gir1.2-ayatanaappindicator3-0.1`, built in on Ubuntu).

## Commands

`rec` is a recording id (folder name), a unique prefix or part of it, or `last`.
Every read command takes `--json`; every command takes `--help`.

| Command | Description |
|---------|-------------|
| `start` / `pause` / `resume` | Record, in segments joined at stop |
| `stop [--trim]` | Stop (`--trim` cuts the trailing silence) |
| `status [--json]` | State and duration, pauses excluded |
| `tray` | Panel icon: start, pause, stop, recent recordings |
| `transcribe [rec\|file]` | WhisperX; `--prompt`, `--speakers N\|MIN-MAX`, `--language`, `--model` |
| `list [--since D] [--limit N]` | All recordings, newest first |
| `show [rec]` | Duration, transcription settings, talk time per speaker |
| `cat [rec] [--speaker S] [--from T] [--to T]` | Print a transcript |
| `search TEXT [--since D] [--speaker S] [-C N]` | Search every transcript (case and accents ignored) |
| `path` / `open` / `rename rec NAME` | Folder path, file manager, rename to `<date>_NAME` |
| `trim [rec]` | Detect the end of the conversation and cut |
| `push [rec]` | Push the transcript to tuls.me |
| `setup` | Configure settings |

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
`audio-recorder` skill: a session finds the meeting, transcribes it with the
mission's vocabulary and participant count, names the speakers, then writes the
minutes where the project lives.

`install.sh` installs and enables it in every Claude Code config dir it finds
(`~/.claude`, `~/.claude-*`). Without it:

```
/plugin install audio-recorder --marketplace AlexisLaporte/audio-recorder
```

The plugin only teaches Claude the CLI: install the CLI itself with `install.sh`.

## Platforms

- **Linux**: PulseAudio
- **macOS**: AVFoundation + [BlackHole](https://github.com/ExistentialAudio/BlackHole) for system audio

## License

MIT
