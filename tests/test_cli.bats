#!/usr/bin/env bats

setup() {
    REPO_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
    export HOME="$(mktemp -d)"
}

teardown() {
    rm -rf "$HOME"
}

@test "every command has its own --help" {
    for cmd in start pause resume stop status tray transcribe trim list show cat search path rename open push setup; do
        run "$REPO_DIR/audio-recorder" "$cmd" --help
        [ "$status" -eq 0 ]
        [[ "${lines[0]}" == "Usage: audio-recorder"* ]]
        [[ "$output" == *"audio-recorder $cmd"* ]]
    done
}

@test "an unknown command fails with the general help" {
    run "$REPO_DIR/audio-recorder" nope
    [ "$status" -eq 1 ]
    [[ "$output" == *"Unknown command: nope"* ]]
}

@test "the plugin manifest names the skill shipped in the repo" {
    [ -f "$REPO_DIR/skills/audio-recorder/SKILL.md" ]
    python3 -c 'import json,sys; assert json.load(open(sys.argv[1]))["name"] == "audio-recorder"' "$REPO_DIR/.claude-plugin/plugin.json"
    head -3 "$REPO_DIR/skills/audio-recorder/SKILL.md" | grep -q '^name: audio-recorder$'
}
