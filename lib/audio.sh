#!/bin/bash
# audio-recorder - Audio recording module
#
# A recording is made of segments: pause ends the current ffmpeg process and
# resume starts a new one, writing audio.part01.mp3, audio.part02.mp3… in the
# recording folder. stop joins them into audio.mp3 without re-encoding.
# The session lives in $STATE_DIR: folder, pid (ffmpeg, while recording),
# segment_start, recorded (seconds of finished segments), paused.

# ─────────────────────────────────────────────────────────────────────────────
# Audio sources
# ─────────────────────────────────────────────────────────────────────────────

get_sources() {
    local devices
    case "$OS" in
        Darwin)
            devices=$(ffmpeg -f avfoundation -list_devices true -i "" 2>&1 | grep -A 100 "audio devices" | grep "^\[")
            # Try Background Music first, then BlackHole
            MONITOR=$(echo "$devices" | grep -i "background music" | head -1 | sed 's/.*\[\([0-9]*\)\].*/\1/')
            [ -z "$MONITOR" ] && MONITOR=$(echo "$devices" | grep -i "blackhole" | head -1 | sed 's/.*\[\([0-9]*\)\].*/\1/')
            MIC=$(echo "$devices" | grep -i "microphone\|macbook" | head -1 | sed 's/.*\[\([0-9]*\)\].*/\1/')
            [ -z "$MIC" ] && MIC="0"
            ;;
        *)
            # Bluetooth needs no special case: the bluez sink monitor captures in
            # both A2DP and HFP, and WirePlumber switches the headset to HFP as
            # soon as its mic is opened, then back to A2DP when released.
            MONITOR="$(pactl get-default-sink).monitor"
            MIC="$(pactl get-default-source)"
            ;;
    esac
}

ensure_mic_active() {
    # Unmute mic if muted
    local muted
    muted=$(pactl get-source-mute "$MIC" 2>/dev/null | awk '{print $2}')
    if [ "$muted" = "oui" ] || [ "$muted" = "yes" ]; then
        pactl set-source-mute "$MIC" 0
        warn "Mic was muted — auto-unmuted"
    fi
}

check_audio_volume() {
    local file="$1"
    local duration="${2:-2}"
    local test_file
    test_file=$(mktemp /tmp/audio-test-XXXXX.wav)
    # Sample last N seconds of the file
    ffmpeg -sseof "-${duration}" -i "$file" -t "$duration" -y "$test_file" &>/dev/null
    local volume
    volume=$(ffmpeg -i "$test_file" -af volumedetect -f null /dev/null 2>&1 | grep mean_volume | awk '{print $5}')
    rm -f "$test_file"
    echo "${volume:--91.0}"
}

# Warns every 2 min while the segment's ffmpeg runs if nothing is captured.
audio_watchdog() {
    local audio_file="$1"
    local ffmpeg_pid="$2"
    local check_interval=120

    sleep "$check_interval"
    while kill -0 "$ffmpeg_pid" 2>/dev/null; do
        local vol
        vol=$(check_audio_volume "$audio_file" 5)
        local vol_int=${vol%%.*}
        if [ "$vol_int" -lt -60 ]; then
            notify "⚠️ Audio may be empty" "Monitor volume: ${vol} dB — check your audio source"
        fi
        sleep "$check_interval"
    done
}

# ─────────────────────────────────────────────────────────────────────────────
# Session state
# ─────────────────────────────────────────────────────────────────────────────

state_get() { cat "$STATE_DIR/$1" 2>/dev/null || true; }
state_set() { mkdir -p "$STATE_DIR" && echo "$2" > "$STATE_DIR/$1"; }

# idle | recording | paused | interrupted (ffmpeg died while recording)
session_state() {
    local pid
    if [ ! -f "$STATE_DIR/folder" ]; then
        echo idle
    elif [ -f "$STATE_DIR/paused" ]; then
        echo paused
    elif pid=$(state_get pid); [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo recording
    else
        echo interrupted
    fi
}

# Seconds recorded so far, pauses excluded.
session_elapsed() {
    local recorded segment_start
    recorded=$(state_get recorded)
    recorded=${recorded:-0}
    segment_start=$(state_get segment_start)
    if [ -n "$segment_start" ] && [ "$(session_state)" = recording ]; then
        recorded=$((recorded + $(date +%s) - segment_start))
    fi
    echo "$recorded"
}

# ─────────────────────────────────────────────────────────────────────────────
# Segments
# ─────────────────────────────────────────────────────────────────────────────

start_segment() {
    local folder="$1"
    get_sources
    [ "$OS" != "Darwin" ] && ensure_mic_active

    local parts part
    parts=$(state_get parts)
    parts=$(( ${parts:-0} + 1 ))
    part="$folder/$(printf 'audio.part%02d.mp3' "$parts")"

    # Mic and system audio stay on separate channels (L/R) instead of being
    # mixed, so transcription can tell the local speaker from the remote ones.
    local dual_track_args=(
        -filter_complex "[0:a]aresample=48000,aformat=channel_layouts=mono[sys];[1:a]aresample=48000,aformat=channel_layouts=mono[mic];[mic][sys]join=inputs=2:channel_layout=stereo:map=0.0-FL|1.0-FR[out]"
        -map "[out]" -metadata comment="$DUAL_TRACK_TAG"
    )

    case "$OS" in
        Darwin)
            if [ -n "$MONITOR" ]; then
                ffmpeg -nostdin -f avfoundation -i ":$MONITOR" -f avfoundation -i ":$MIC" \
                    "${dual_track_args[@]}" -codec:a libmp3lame -q:a 2 "$part" &>/dev/null &
            else
                ffmpeg -nostdin -f avfoundation -i ":$MIC" -codec:a libmp3lame -q:a 2 \
                    "$part" &>/dev/null &
            fi
            ;;
        *)
            ffmpeg -nostdin -f pulse -i "$MONITOR" -f pulse -i "$MIC" \
                "${dual_track_args[@]}" -codec:a libmp3lame -q:a 2 "$part" &>/dev/null &
            ;;
    esac
    local pid=$!

    state_set pid "$pid"
    state_set parts "$parts"
    state_set segment_start "$(date +%s)"

    if [ "$OS" != "Darwin" ]; then
        audio_watchdog "$part" "$pid" &
        state_set watchdog_pid "$!"
        disown
    fi
}

# Ends the running segment and waits for ffmpeg to finalize its file.
end_segment() {
    local watchdog pid segment_start recorded
    watchdog=$(state_get watchdog_pid)
    if [ -n "$watchdog" ]; then
        kill "$watchdog" 2>/dev/null
    fi
    rm -f "$STATE_DIR/watchdog_pid"

    pid=$(state_get pid)
    if [ -n "$pid" ] && kill "$pid" 2>/dev/null; then
        local i
        for ((i = 0; i < 100; i++)); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.1
        done
        if kill -0 "$pid" 2>/dev/null; then
            warn "ffmpeg did not exit after 10 s — killing it"
            kill -9 "$pid" 2>/dev/null
        fi
    fi

    segment_start=$(state_get segment_start)
    recorded=$(state_get recorded)
    if [ -n "$segment_start" ]; then
        state_set recorded $(( ${recorded:-0} + $(date +%s) - segment_start ))
    fi
    rm -f "$STATE_DIR/pid" "$STATE_DIR/segment_start"
}

# Joins audio.partNN.mp3 into audio.mp3 (stream copy, dual-track tag kept).
join_parts() {
    local folder="$1"
    local parts=("$folder"/audio.part*.mp3)
    if [ ! -e "${parts[0]}" ]; then
        error "No audio recorded in $folder"
        return 1
    fi
    if [ ${#parts[@]} -eq 1 ]; then
        mv "${parts[0]}" "$folder/audio.mp3"
        return
    fi

    local list="$folder/parts.txt" comment p
    comment=$(ffprobe -v error -show_entries format_tags=comment -of default=nw=1:nk=1 "${parts[0]}")
    for p in "${parts[@]}"; do
        echo "file '$(basename "$p")'"
    done > "$list"
    if ! ffmpeg -v error -nostdin -f concat -safe 0 -i "$list" -c copy \
        -metadata comment="$comment" "$folder/audio.mp3"; then
        rm -f "$folder/audio.mp3"
        error "Failed to join the recording parts (kept in $folder)"
        return 1
    fi
    rm -f "${parts[@]}" "$list"
}

# ─────────────────────────────────────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────────────────────────────────────

cmd_start() {
    local current
    current=$(session_state)
    if [ "$current" != idle ]; then
        error "A recording is already $current: $(basename "$(state_get folder)") — stop it first"
        return 1
    fi

    local name folder
    name="recording_$(date +%Y%m%d_%H%M%S)"
    folder="$RECORDINGS_DIR/$name"
    mkdir -p "$folder"
    recordings meta-set "$folder" "started_at=$(date -Iseconds)"

    rm -rf "$STATE_DIR"
    state_set folder "$folder"
    state_set recorded 0
    start_segment "$folder"

    local audio_mode sink_name mic_name
    case "$OS" in
        Darwin) sink_name="AVFoundation :$MONITOR"; mic_name="AVFoundation :$MIC"; audio_mode="macOS" ;;
        *)
            sink_name=$(pactl get-default-sink)
            mic_name="$MIC"
            if echo "$sink_name" | grep -qi "bluez"; then
                audio_mode="🎧 Bluetooth"
            else
                audio_mode="🔊 Speakers + Mic"
            fi
            ;;
    esac

    echo ""
    echo -e "  ${RED}●${NC} ${BOLD}Recording${NC}  ${YELLOW}${audio_mode}${NC}"
    echo -e "  ${DIM}$name${NC}"
    echo -e "  ${DIM}⏎ ${sink_name}${NC}"
    echo -e "  ${DIM}⏎ ${mic_name}${NC}"
    echo ""

    notify "Recording started" "$name"
}

cmd_pause() {
    if [ "$(session_state)" != recording ]; then
        error "Not recording ($(session_state))"
        return 1
    fi
    end_segment
    touch "$STATE_DIR/paused"
    local elapsed
    elapsed=$(format_duration "$(session_elapsed)")
    echo -e "  ${YELLOW}❚❚${NC} ${BOLD}Paused${NC} ${DIM}($elapsed recorded)${NC}"
    notify "Recording paused" "$elapsed recorded"
}

cmd_resume() {
    if [ "$(session_state)" != paused ]; then
        error "Not paused ($(session_state))"
        return 1
    fi
    rm -f "$STATE_DIR/paused"
    start_segment "$(state_get folder)"
    echo -e "  ${RED}●${NC} ${BOLD}Recording${NC} ${DIM}(resumed)${NC}"
    notify "Recording resumed" "$(basename "$(state_get folder)")"
}

cmd_stop() {
    local do_trim=false
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --trim) do_trim=true; shift ;;
            *) error "Unknown option: $1"; return 1 ;;
        esac
    done

    local current folder
    current=$(session_state)
    case "$current" in
        idle) error "No recording in progress"; return 1 ;;
        recording) end_segment ;;
        interrupted) warn "ffmpeg had stopped on its own — keeping what was recorded" ;;
    esac
    folder=$(state_get folder)

    join_parts "$folder" || return 1
    rm -rf "$STATE_DIR"

    local seconds
    seconds=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$folder/audio.mp3")
    seconds=${seconds%%.*}
    recordings meta-set "$folder" "duration_s:=$seconds"

    echo ""
    echo -e "  ${GREEN}■${NC} ${BOLD}Stopped${NC} ${DIM}($(format_duration "$seconds"))${NC}"
    echo -e "  ${DIM}$folder/audio.mp3${NC}"
    notify "Recording stopped" "$(basename "$folder") ($(format_duration "$seconds"))"

    if [ "$do_trim" = true ]; then
        cmd_trim --yes "$folder"
    fi
}

cmd_status() {
    local json=false
    [ "$1" = "--json" ] && json=true

    local current folder elapsed
    current=$(session_state)
    folder=$(state_get folder)
    elapsed=$(session_elapsed)

    if [ "$json" = true ]; then
        if [ "$current" = idle ]; then
            echo '{"state": "idle"}'
        else
            printf '{"state": "%s", "id": "%s", "path": "%s", "elapsed_s": %d}\n' \
                "$current" "$(basename "$folder")" "$folder" "$elapsed"
        fi
        return
    fi

    case "$current" in
        idle)        echo -e "${DIM}■${NC} Not recording" ;;
        recording)   echo -e "${RED}●${NC} Recording: ${BOLD}$(basename "$folder")${NC} ($(format_duration "$elapsed"))" ;;
        paused)      echo -e "${YELLOW}❚❚${NC} Paused: ${BOLD}$(basename "$folder")${NC} ($(format_duration "$elapsed") recorded)" ;;
        interrupted) echo -e "${RED}✗${NC} Interrupted: ${BOLD}$(basename "$folder")${NC} — ffmpeg stopped, run 'audio-recorder stop'" ;;
    esac
}
