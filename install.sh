#!/bin/bash
# audio-recorder installer
#
# Modes:
#   ./install.sh            Install (copy payload to a stable location, then link a launcher).
#                           Survives moving/deleting this checkout. Default — use for end users.
#   ./install.sh --link     Dev install: symlink the launcher to THIS checkout (edits go live).
#   ./install.sh --uninstall  Remove launcher, completion, skill, tray autostart and the payload.
#                             Keeps your config (~/.config/audio-recorder) and recordings.
#   -y, --yes               Assume "yes" to prompts (deps). For non-interactive installs.

set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HOME/.local/bin"
LIBEXEC_DIR="$HOME/.local/share/audio-recorder"
COMPLETION_DIR="$HOME/.local/share/bash-completion/completions"
AUTOSTART_FILE="$HOME/.config/autostart/audio-recorder-tray.desktop"
APP_FILE="$HOME/.local/share/applications/audio-recorder.desktop"
RECORDINGS_DIR="$HOME/Recordings"

OS="$(uname -s)"

MODE="copy"        # copy | link
DO_UNINSTALL=0
ASSUME_YES=0

# Payload that makes up a working install (relative to REPO_DIR).
PAYLOAD=(audio-recorder lib icons audio-recorder.bash-completion .claude-plugin skills)

# Each Claude Code account has its own config dir (~/.claude, ~/.claude-<account>),
# where the plugin is enabled.
claude_dirs() {
    local d
    for d in "$HOME"/.claude "$HOME"/.claude-*; do
        [ -f "$d/settings.json" ] && echo "$d"
    done
}

usage() {
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
}

# ─────────────────────────────────────────────────────────────────────────────
# Args
# ─────────────────────────────────────────────────────────────────────────────

while [ $# -gt 0 ]; do
    case "$1" in
        --link)      MODE="link" ;;
        --copy)      MODE="copy" ;;
        --uninstall) DO_UNINSTALL=1 ;;
        -y|--yes)    ASSUME_YES=1 ;;
        -h|--help)   usage; exit 0 ;;
        *)           echo "Unknown option: $1"; echo; usage; exit 1 ;;
    esac
    shift
done

confirm() {
    # confirm "prompt"  -> 0 if yes. Defaults to yes. Honors --yes / non-tty.
    [ "$ASSUME_YES" = 1 ] && return 0
    [ -t 0 ] || return 0
    local reply
    read -rp "$1 [Y/n] " reply
    [[ ! "$reply" =~ ^[Nn] ]]
}

# ─────────────────────────────────────────────────────────────────────────────
# Uninstall
# ─────────────────────────────────────────────────────────────────────────────

if [ "$DO_UNINSTALL" = 1 ]; then
    echo "=== audio-recorder uninstall ==="
    rm -f "$BIN_DIR/audio-recorder"
    rm -f "$COMPLETION_DIR/audio-recorder" "$AUTOSTART_FILE" "$APP_FILE"
    pkill -f "lib/tray.py" 2>/dev/null || true
    if command -v claude &>/dev/null; then
        for d in $(claude_dirs); do
            CLAUDE_CONFIG_DIR="$d" claude plugin uninstall audio-recorder@audio-recorder &>/dev/null || true
        done
        claude plugin marketplace remove audio-recorder &>/dev/null || true
    fi
    rm -rf "$LIBEXEC_DIR"
    echo "[OK] Removed launcher, completion, tray autostart, Claude plugin and $LIBEXEC_DIR"
    echo "[KEPT] Config (~/.config/audio-recorder) and recordings ($RECORDINGS_DIR)"
    exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
# Install
# ─────────────────────────────────────────────────────────────────────────────

echo "=== audio-recorder installer (mode: $MODE) ==="
echo ""

mkdir -p "$BIN_DIR" "$COMPLETION_DIR"

if [ "$MODE" = "copy" ]; then
    # Copy the payload to a stable location so the install survives this
    # checkout being moved or deleted (and so the release tarball flow works).
    rm -rf "$LIBEXEC_DIR"
    mkdir -p "$LIBEXEC_DIR"
    for item in "${PAYLOAD[@]}"; do
        [ -e "$REPO_DIR/$item" ] && cp -R "$REPO_DIR/$item" "$LIBEXEC_DIR/"
    done
    SRC="$LIBEXEC_DIR"
    echo "[OK] Payload copied to $LIBEXEC_DIR"
else
    SRC="$REPO_DIR"
    echo "[OK] Linking to checkout $REPO_DIR (edits go live)"
fi

# Launcher resolves its real path (readlink -f) to find lib/, so a symlink is fine in both modes.
ln -sf "$SRC/audio-recorder" "$BIN_DIR/audio-recorder"
echo "[OK] Launcher: $BIN_DIR/audio-recorder -> $SRC/audio-recorder"

ln -sf "$SRC/audio-recorder.bash-completion" "$COMPLETION_DIR/audio-recorder"
echo "[OK] Bash completion installed"

# PATH check
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo ""
    echo "[WARN] $BIN_DIR is not in your PATH. Add to your ~/.bashrc or ~/.zshrc:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# ─────────────────────────────────────────────────────────────────────────────
# Dependencies
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "=== Checking dependencies ==="

install_deps() {
    confirm "Install missing dependencies?" || return

    case "$OS" in
        Darwin) echo "Installing ffmpeg..."; brew install ffmpeg || true ;;
        *)      echo "Installing ffmpeg..."; sudo apt install -y ffmpeg || true ;;
    esac

    # WhisperX via uv on a pinned Python — its deps cap at Python <3.13, so we
    # can't rely on the system Python on recent distros (e.g. Ubuntu 26.04).
    if ! command -v whisperx &>/dev/null; then
        echo "Installing whisperx..."
        if ! command -v uv &>/dev/null; then
            echo "Installing uv (Python toolchain manager)..."
            curl -LsSf https://astral.sh/uv/install.sh | sh
            export PATH="$HOME/.local/bin:$PATH"
        fi
        if command -v uv &>/dev/null; then
            uv tool install whisperx --python 3.12
        else
            echo "[ERROR] uv install failed. Install whisperx manually:"
            echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
            echo "  uv tool install whisperx --python 3.12"
        fi
    fi
}

MISSING=0
if command -v ffmpeg &>/dev/null; then echo "[OK] ffmpeg"; else echo "[MISSING] ffmpeg"; MISSING=1; fi
if command -v whisperx &>/dev/null; then echo "[OK] whisperx"; else echo "[MISSING] whisperx"; MISSING=1; fi

[ $MISSING -eq 1 ] && install_deps

if [ "$OS" = "Darwin" ]; then
    echo ""
    echo "=== macOS note ==="
    echo "For system audio capture, install BlackHole:"
    echo "  brew install blackhole-2ch"
    echo "Then set up a Multi-Output Device in Audio MIDI Setup."
fi

# ─────────────────────────────────────────────────────────────────────────────
# Claude Code plugin
# ─────────────────────────────────────────────────────────────────────────────

# The install itself is the marketplace (a directory source, read in place);
# the plugin is cached by version, so bump .claude-plugin/plugin.json to ship
# a skill change.
install_plugin() {
    local d="$1"
    export CLAUDE_CONFIG_DIR="$d"
    # Former installs linked the skill by hand
    rm -f "$d/skills/audio-recorder.md" "$d/skills/audio-recorder/SKILL.md"
    rmdir "$d/skills/audio-recorder" 2>/dev/null || true

    if claude plugin marketplace list --json | grep -q '"name": "audio-recorder"'; then
        claude plugin marketplace update audio-recorder >/dev/null
    else
        claude plugin marketplace add "$SRC" >/dev/null
    fi
    if claude plugin list --json | grep -q '"id": "audio-recorder@audio-recorder"'; then
        claude plugin update audio-recorder@audio-recorder >/dev/null
    else
        claude plugin install audio-recorder@audio-recorder --scope user >/dev/null
    fi
}

echo ""
echo "=== Claude Code plugin ==="
if ! command -v claude &>/dev/null; then
    echo "[SKIP] claude not found"
else
    for d in $(claude_dirs); do
        if (install_plugin "$d"); then
            echo "[OK] Plugin audio-recorder enabled in $d"
        else
            echo "[FAIL] Plugin not installed in $d"
        fi
    done
fi

# ─────────────────────────────────────────────────────────────────────────────
# Panel icon (Linux)
# ─────────────────────────────────────────────────────────────────────────────

if [ "$OS" != "Darwin" ]; then
    echo ""
    echo "=== Panel icon ==="
    if python3 -c "import gi; gi.require_version('AyatanaAppIndicator3', '0.1')" 2>/dev/null; then
        mkdir -p "$(dirname "$AUTOSTART_FILE")" "$(dirname "$APP_FILE")"
        cat > "$APP_FILE" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Audio Recorder
Comment=Record meetings: panel icon to start, pause and stop
Exec=$BIN_DIR/audio-recorder tray
Icon=$SRC/icons/audio-recorder.svg
Categories=AudioVideo;Audio;
Keywords=record;meeting;transcript;
DESKTOP
        echo "[OK] App menu entry ($APP_FILE)"
        cat > "$AUTOSTART_FILE" <<DESKTOP
[Desktop Entry]
Type=Application
Name=audio-recorder
Comment=Start, pause and stop meeting recordings
Exec=$BIN_DIR/audio-recorder tray
Icon=$SRC/icons/audio-recorder.svg
X-GNOME-Autostart-enabled=true
DESKTOP
        echo "[OK] Starts with the session ($AUTOSTART_FILE)"
        echo "     Now: audio-recorder tray &"
    else
        echo "[SKIP] AppIndicator missing: sudo apt install gir1.2-ayatanaappindicator3-0.1"
        echo "       GNOME also needs the AppIndicator extension (built in on Ubuntu)."
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "=== Setup ==="
echo "Run 'audio-recorder setup' to configure your HuggingFace token."
echo ""
echo "WhisperX requires accepting the conditions of:"
echo "  - https://hf.co/pyannote/speaker-diarization-community-1"
echo ""
echo "Done!"
