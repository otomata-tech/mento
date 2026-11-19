#!/bin/bash
# audio-recorder - Utilities module
# shellcheck disable=SC2034  # Variables used by other sourced modules

# ─────────────────────────────────────────────────────────────────────────────
# Colors
# ─────────────────────────────────────────────────────────────────────────────

# Colors (disabled if not a terminal)
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BLUE='\033[0;34m'
    BOLD='\033[1m'
    DIM='\033[2m'
    NC='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' BOLD='' DIM='' NC=''
fi

# ─────────────────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────────────────

info()    { echo -e "${BLUE}▸${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
error()   { echo -e "${RED}✗${NC} $*" >&2; }
warn()    { echo -e "${YELLOW}!${NC} $*"; }

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

spinner() {
    local pid="$1"
    local msg="$2"
    local chars="⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    local i=0
    while kill -0 "$pid" 2>/dev/null; do
        printf "\r${BLUE}${chars:$i:1}${NC} %s" "$msg"
        i=$(( (i + 1) % 10 ))
        sleep 0.1
    done
    printf "\r"
}

format_duration() {
    local seconds="$1"
    if [ "$seconds" -lt 60 ]; then
        echo "${seconds}s"
    elif [ "$seconds" -lt 3600 ]; then
        echo "$((seconds / 60))m $((seconds % 60))s"
    else
        echo "$((seconds / 3600))h $((seconds % 3600 / 60))m"
    fi
}

notify() {
    local title="$1"
    local msg="$2"
    case "$OS" in
        Darwin) osascript -e "display notification \"$msg\" with title \"$title\"" 2>/dev/null ;;
        *)      notify-send "$title" "$msg" -i audio-input-microphone 2>/dev/null ;;
    esac
}

RECORDINGS_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/recordings.py"

# Recordings library (lib/recordings.py): resolve, list, show, cat, search…
recordings() {
    python3 -I "$RECORDINGS_PY" --root "$RECORDINGS_DIR" "$@"
}

# Path of a recording from its id, unique prefix or substring, or "last".
resolve_recording() {
    recordings resolve "$1"
}

# ─────────────────────────────────────────────────────────────────────────────
# Help
# ─────────────────────────────────────────────────────────────────────────────

show_help() {
    echo -e "${BOLD}audio-recorder${NC} - Record & transcribe meetings"
    echo ""
    echo "Usage: audio-recorder <command> [options]"
    echo ""
    echo "Recording:"
    echo "  start                    Start recording (mic left, system audio right)"
    echo "  pause / resume           Pause or resume the current recording"
    echo "  stop [--trim]            Stop (--trim: cut the trailing silence)"
    echo "  status [--json]          Recording state and duration"
    echo "  tray                     Panel icon: start, pause, stop"
    echo ""
    echo "Transcription:"
    echo "  transcribe [rec|file]    Transcribe with WhisperX"
    echo "      --prompt TEXT        Vocabulary to prime (added to WHISPER_INITIAL_PROMPT)"
    echo "      --speakers N|MIN-MAX Number of participants"
    echo "      --language CODE      Force the language (default: WHISPER_LANGUAGE)"
    echo "      --model NAME         Whisper model (default: WHISPER_MODEL)"
    echo "  trim [rec] [--yes]       Detect the end of the conversation and cut"
    echo ""
    echo "Library (rec = id, unique prefix or part of it, or 'last'):"
    echo "  list [--since D] [--limit N] [--json]"
    echo "  show [rec] [--json]      Details, transcription settings, speakers"
    echo "  cat [rec] [--speaker S] [--from T] [--to T] [--json]"
    echo "  search TEXT [--since D] [--speaker S] [-C N] [--json]"
    echo "  path [rec]               Folder path"
    echo "  rename rec NAME          Rename to <date>_NAME ([a-z0-9_], 50 chars)"
    echo "  open [rec]               Open the folder in the file manager"
    echo "  push [rec] [--with-audio]  Push the transcript to tuls.me"
    echo ""
    echo "  setup                    Configure settings"
    echo ""
    echo "Dates: YYYY-MM-DD or Nd (7d = last 7 days). Times: SS, MM:SS or H:MM:SS."
    echo "Details and examples: audio-recorder <command> --help"
}

# Per-command help: audio-recorder <command> --help
command_help() {
    case "$1" in
        start) cat <<'TXT'
Usage: audio-recorder start

Start recording: mic on the left channel, system audio on the right.
Follows the default sink and source (pactl); unmutes the mic if needed.
Creates <RECORDINGS_DIR>/recording_YYYYMMDD_HHMMSS/.
TXT
        ;;
        pause|resume) cat <<'TXT'
Usage: audio-recorder pause
       audio-recorder resume

Pause ends the current segment (audio.partNN.mp3); resume starts a new one.
stop joins the segments into audio.mp3. Pauses are left out of the duration.
TXT
        ;;
        stop) cat <<'TXT'
Usage: audio-recorder stop [--trim]

Stop and join the segments into audio.mp3. Does not transcribe: run
'audio-recorder transcribe' with the meeting's vocabulary and participants.
  --trim   detect the end of the conversation and cut the trailing audio
           (original kept as audio_full.mp3)
TXT
        ;;
        status) cat <<'TXT'
Usage: audio-recorder status [--json]

State (idle, recording, paused, interrupted) and recorded time.
  --json   {"state", "id", "path", "elapsed_s"}
TXT
        ;;
        tray) cat <<'TXT'
Usage: audio-recorder tray

Panel icon (Linux, AppIndicator): start, pause, resume, stop, recent
recordings. One instance at a time; install.sh starts it with the session.
TXT
        ;;
        transcribe) cat <<'TXT'
Usage: audio-recorder transcribe [rec|file] [options]

Transcribe with WhisperX and diarization into transcript.txt
([mm:ss] SPEAKER: text). Dual-track recordings pin LOCAL_SPEAKER to the mic.
  --prompt TEXT          vocabulary to prime (names, products, acronyms),
                         added after WHISPER_INITIAL_PROMPT; ~30-50 terms max
  --speakers N|MIN-MAX   participants, local speaker included
  --language CODE        force the language (default: WHISPER_LANGUAGE)
  --model NAME           Whisper model (default: WHISPER_MODEL)
Settings are kept in meta.json. Takes minutes of GPU: run it in background.

Example:
  audio-recorder transcribe last --prompt "Acme, DSN, Marie Dupont" --speakers 3
TXT
        ;;
        trim|cut) cat <<'TXT'
Usage: audio-recorder trim [rec] [--yes]

Find the end of the conversation (first long silence after 5 min) and cut
there; the original is kept as audio_full.mp3. --yes skips the confirmation.
TXT
        ;;
        list|ls) cat <<'TXT'
Usage: audio-recorder list [--since D] [--limit N] [--json]

All recordings, newest first: id, date, duration, state, speakers.
  --since D   YYYY-MM-DD or Nd (7d = last 7 days)
TXT
        ;;
        show) cat <<'TXT'
Usage: audio-recorder show [rec] [--json]

Path, date, duration, files, transcription settings, and lines and
approximate talk time per speaker.
TXT
        ;;
        cat) cat <<'TXT'
Usage: audio-recorder cat [rec] [--speaker S] [--from T] [--to T] [--json]

Print a transcript, optionally one speaker or a time range.
Times: SS, MM:SS or H:MM:SS.

Example:
  audio-recorder cat last --speaker SPEAKER_01 --from 10:00 --to 25:00
TXT
        ;;
        search) cat <<'TXT'
Usage: audio-recorder search TEXT [--since D] [--speaker S] [-C N] [--json]

Search every transcript, case and accents ignored. One hit per line:
<id> [mm:ss] SPEAKER: text. -C N adds N lines of context (hits marked >).
Exits 1 when nothing matches.

Example:
  audio-recorder search "budget" --since 30d -C 2
TXT
        ;;
        path) cat <<'TXT'
Usage: audio-recorder path [rec]

Print the recording's folder (audio.mp3, transcript.txt, meta.json).
TXT
        ;;
        rename) cat <<'TXT'
Usage: audio-recorder rename rec NAME

Rename to <date>_NAME, keeping the date prefix. NAME: a-z 0-9 _, 50 chars max.

Example:
  audio-recorder rename last point_client_onboarding
TXT
        ;;
        open) cat <<'TXT'
Usage: audio-recorder open [rec]

Open the recording's folder in the file manager.
TXT
        ;;
        push) cat <<'TXT'
Usage: audio-recorder push [rec] [--with-audio]

Push the transcript to tuls.me (TULS_API_TOKEN), with the mp3 if asked.
TXT
        ;;
        setup) cat <<'TXT'
Usage: audio-recorder setup

Interactive configuration, saved to ~/.config/audio-recorder/config:
HF_TOKEN, RECORDINGS_DIR, WHISPER_MODEL, LOCAL_SPEAKER, WHISPER_LANGUAGE,
WHISPER_INITIAL_PROMPT, TULS_API_TOKEN.
TXT
        ;;
        *) show_help ;;
    esac
    case "$1" in
        transcribe|trim|cut|show|cat|path|rename|open|push)
            echo ""
            echo "rec: recording id (folder name), a unique prefix or part of it, or 'last'."
            ;;
    esac
}
