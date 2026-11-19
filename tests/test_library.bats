#!/usr/bin/env bats

setup() {
    REPO_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
    ROOT="$(mktemp -d)"
    mkdir -p "$ROOT/20261117_point_client_onboarding" "$ROOT/20261118_atelier_paie" \
        "$ROOT/recording_20261119_093000" "$ROOT/not_a_recording"
    cat > "$ROOT/20261117_point_client_onboarding/transcript.txt" <<'EOF'
[00:05] Alexis: Bonjour, on fait le point sur la réunion.
[00:12] SPEAKER_01: Oui, la DSN est prête.
[01:30] Alexis: Parfait.
[62:10] SPEAKER_01: On se revoit jeudi.
EOF
    echo '{"duration_s": 3800}' > "$ROOT/20261117_point_client_onboarding/meta.json"
    cat > "$ROOT/20261118_atelier_paie/transcript.txt" <<'EOF'
[00:01] SPEAKER_00: Reunion paie, sans accent.
EOF
    echo '{"duration_s": 60}' > "$ROOT/20261118_atelier_paie/meta.json"
    touch "$ROOT/recording_20261119_093000/audio.part01.mp3"
}

teardown() {
    rm -rf "$ROOT"
}

rec() {
    run python3 -I "$REPO_DIR/lib/recordings.py" --root "$ROOT" "$@"
}

@test "list sorts recordings newest first and skips other folders" {
    rec list --json
    [ "$status" -eq 0 ]
    ids=$(echo "$output" | python3 -c 'import json,sys; print(" ".join(r["id"] for r in json.load(sys.stdin)))')
    [ "$ids" = "recording_20261119_093000 20261118_atelier_paie 20261117_point_client_onboarding" ]
    [[ "$output" == *'"state": "in_progress"'* ]]
}

@test "list --since filters by date" {
    rec list --since 2026-11-18
    [ "$status" -eq 0 ]
    [[ "$output" != *onboarding* ]]
    [[ "$output" == *atelier_paie* ]]
}

@test "resolve accepts last, a prefix and a unique substring" {
    rec resolve last
    [ "$output" = "$ROOT/recording_20261119_093000" ]
    rec resolve 20261117
    [ "$output" = "$ROOT/20261117_point_client_onboarding" ]
    rec resolve paie
    [ "$output" = "$ROOT/20261118_atelier_paie" ]
}

@test "resolve rejects an ambiguous reference" {
    rec resolve 202611
    [ "$status" -eq 1 ]
    [[ "$output" == *"matches several"* ]]
}

@test "search ignores case and accents" {
    rec search "REUNION"
    [ "$status" -eq 0 ]
    [[ "$output" == *"20261117_point_client_onboarding [00:05] Alexis: Bonjour"* ]]
    [[ "$output" == *"20261118_atelier_paie [00:01] SPEAKER_00: Reunion paie"* ]]
}

@test "search filters by speaker and fails without a match" {
    rec search reunion --speaker alexis
    [[ "$output" != *atelier_paie* ]]
    rec search introuvable
    [ "$status" -eq 1 ]
}

@test "cat filters by speaker and time range, hours included" {
    rec cat onboarding --speaker SPEAKER_01 --from 0:10 --to 1:10:00
    [ "${lines[0]}" = "[00:12] SPEAKER_01: Oui, la DSN est prête." ]
    [ "${lines[1]}" = "[1:02:10] SPEAKER_01: On se revoit jeudi." ]
    [ "${#lines[@]}" -eq 2 ]
}

@test "show counts lines and talk time per speaker" {
    rec show onboarding --json
    [ "$status" -eq 0 ]
    alexis=$(echo "$output" | python3 -c 'import json,sys; print([s for s in json.load(sys.stdin)["speakers"] if s["name"]=="Alexis"][0]["lines"])')
    [ "$alexis" -eq 2 ]
}

@test "rename keeps the date prefix and checks the name" {
    rec rename recording_20261119_093000 "Bad Name"
    [ "$status" -eq 1 ]
    rec rename recording_20261119_093000 point_equipe
    [ "$output" = "$ROOT/20261119_point_equipe" ]
    [ -d "$ROOT/20261119_point_equipe" ]
}

@test "meta-set merges strings, JSON values and dotted keys" {
    rec meta-set "$ROOT/20261118_atelier_paie" transcription.model=large-v3 transcription.dual_track:=true
    python3 - "$ROOT/20261118_atelier_paie/meta.json" <<'EOF'
import json, sys
m = json.load(open(sys.argv[1]))
assert m == {"duration_s": 60, "transcription": {"model": "large-v3", "dual_track": True}}, m
EOF
}
