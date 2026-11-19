#!/bin/bash
# audio-recorder - Library module: browse, read and search recordings.
# The work is done by lib/recordings.py; these commands add what needs the
# shell (the active session, the file manager).

cmd_list()   { recordings list "$@"; }
cmd_show()   { recordings show "$@"; }
cmd_cat()    { recordings cat "$@"; }
cmd_search() { recordings search "$@"; }

cmd_path() {
    resolve_recording "$1"
}

cmd_rename() {
    if [ $# -ne 2 ]; then
        error "Usage: audio-recorder rename <rec> <name>"
        return 1
    fi
    local folder
    folder=$(resolve_recording "$1") || return 1
    if [ "$folder" = "$(state_get folder)" ]; then
        error "Cannot rename the recording in progress — stop it first"
        return 1
    fi
    recordings rename "$folder" "$2"
}

cmd_open() {
    local folder
    folder=$(resolve_recording "$1") || return 1
    case "$OS" in
        Darwin) open "$folder" ;;
        *)      xdg-open "$folder" 2>/dev/null || error "Could not open folder" ;;
    esac
}
