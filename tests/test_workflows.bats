#!/usr/bin/env bats

setup() {
    REPO_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
    source "$REPO_DIR/lib/utils.sh"
    source "$REPO_DIR/lib/audio.sh"
    source "$REPO_DIR/lib/library.sh"

    TEST_TMPDIR="$(mktemp -d)"
    RECORDINGS_DIR="$TEST_TMPDIR/recordings"
    STATE_DIR="$TEST_TMPDIR/state"
    DUAL_TRACK_TAG="audio-recorder:L=mic,R=system"
    OS=Linux
    mkdir -p "$RECORDINGS_DIR"

    notify() { :; }
    # A fake segment: 1 s of tone in place of the ffmpeg capture
    start_segment() {
        local folder="$1" parts part
        parts=$(state_get parts)
        parts=$(( ${parts:-0} + 1 ))
        part="$folder/$(printf 'audio.part%02d.mp3' "$parts")"
        ffmpeg -v error -f lavfi -i "sine=f=$((200 * parts)):d=1" -ac 2 \
            -metadata comment="$DUAL_TRACK_TAG" -y "$part"
        sleep 60 &
        state_set pid "$!"
        state_set parts "$parts"
        state_set segment_start "$(date +%s)"
    }
}

teardown() {
    local pid
    pid=$(state_get pid)
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
    rm -rf "$TEST_TMPDIR"
}

@test "status is idle without a session" {
    run cmd_status --json
    [ "$output" = '{"state": "idle"}' ]
}

@test "start, pause, resume, stop joins the parts into one dual-track audio.mp3" {
    cmd_start >/dev/null
    [ "$(session_state)" = recording ]

    cmd_pause >/dev/null
    [ "$(session_state)" = paused ]
    run cmd_start
    [ "$status" -eq 1 ]

    cmd_resume >/dev/null
    [ "$(session_state)" = recording ]

    cmd_stop >/dev/null
    [ "$(session_state)" = idle ]

    folder=$(resolve_recording last)
    [ -f "$folder/audio.mp3" ]
    ! ls "$folder"/audio.part*.mp3 2>/dev/null
    duration=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$folder/audio.mp3")
    [ "${duration%%.*}" -eq 2 ]
    [ "$(ffprobe -v error -show_entries format_tags=comment -of default=nw=1:nk=1 "$folder/audio.mp3")" = "$DUAL_TRACK_TAG" ]
    grep -q '"duration_s": 2' "$folder/meta.json"
    grep -q '"started_at"' "$folder/meta.json"
}

@test "pause and resume refuse the wrong state" {
    run cmd_pause
    [ "$status" -eq 1 ]
    cmd_start >/dev/null
    run cmd_resume
    [ "$status" -eq 1 ]
}

@test "stop keeps what was recorded when ffmpeg died" {
    cmd_start >/dev/null
    kill "$(state_get pid)"
    sleep 0.2
    [ "$(session_state)" = interrupted ]

    run cmd_stop
    [ "$status" -eq 0 ]
    [ -f "$(resolve_recording last)/audio.mp3" ]
}

@test "status excludes pauses from the elapsed time" {
    cmd_start >/dev/null
    state_set segment_start $(( $(date +%s) - 30 ))
    cmd_pause >/dev/null
    state_set recorded 30
    run cmd_status --json
    [[ "$output" == *'"state": "paused"'* ]]
    [[ "$output" == *'"elapsed_s": 30'* ]]
}

@test "rename refuses the recording in progress" {
    cmd_start >/dev/null
    run cmd_rename last point_client
    [ "$status" -eq 1 ]
    [[ "$output" == *"in progress"* ]]
}
